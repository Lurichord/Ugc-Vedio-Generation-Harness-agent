from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..agents.asset_agent import AssetAgent, AssetArtifact
from ..agents.asset_agent.capabilities import AssetCapabilities, AssetProvider
from ..agents.asset_agent.image_analysis import BasicImageAnalyzer
from ..agents.asset_agent.image_tools import ImagePreparationCapabilities
from ..agents.editorial_agent.models import EditorialArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..evaluators.asset_critic import AssetCritic, ImageAnalyzer
from ..tools.registry import ToolRegistry
from .dependencies import DependencyGraph
from .dependency_builders import asset_commits
from .models import (
    AgentResult,
    EvaluationResult,
    ProjectState,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
    TransitionRecord,
)
from .repair import repair_input_hash, select_repair_commits
from .trajectory import record_task, task_kind_for
from .transitions import transition_after_review


class AssetRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    transition: TransitionRecord
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class AssetHarnessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: AssetArtifact
    record: AssetRunRecord


class AssetHarnessController:
    def __init__(self, agent: AssetAgent, critic: AssetCritic) -> None:
        self.agent = agent
        self.critic = critic

    @classmethod
    def from_provider(
        cls,
        provider: AssetProvider,
        image_analyzer: ImageAnalyzer | None = None,
    ) -> "AssetHarnessController":
        tools = ToolRegistry()
        tools.register(
            AssetAgent.ACQUIRE_TOOL,
            AssetCapabilities(provider).acquire_requirement,
        )
        tools.register(
            AssetAgent.PREPARE_TOOL,
            ImagePreparationCapabilities().prepare_image,
        )
        return cls(
            AssetAgent(tools),
            AssetCritic(image_analyzer or BasicImageAnalyzer()),
        )

    def create_task(
        self,
        editorial: EditorialArtifact,
        voice: VoiceArtifact,
        state: ProjectState,
    ) -> TaskEnvelope:
        visual_refs = [
            f"visual_requirement:{item.visual_request_id}"
            for item in editorial.editorial_plan.visual_requirements
        ]
        graph = DependencyGraph(state.dependency_graph)
        return TaskEnvelope(
            task_id=(
                f"task_asset_{editorial.project_id}_v{state.video.state_version}"
            ),
            agent="asset_agent",
            goal="按 first-success 规则为每个 VisualRequirement 获取一份可用素材",
            scope=TaskScope(
                project_id=editorial.project_id,
                beat_ids=[
                    item.beat_id
                    for item in editorial.editorial_plan.visual_requirements
                ],
                visual_request_ids=[
                    item.visual_request_id
                    for item in editorial.editorial_plan.visual_requirements
                ],
            ),
            based_on_state_version=state.video.state_version,
            allowed_tools=[AssetAgent.ACQUIRE_TOOL],
            forbidden_actions=[
                "modify_narrative",
                "modify_voice",
                "modify_editorial_plan",
                "modify_timeline",
                "render_video",
            ],
            acceptance_criteria=[
                "每个 VisualRequirement 都有且只有一个最终 resolution",
                "严格执行 first-success，不保留 top-k 候选",
                "所有本地素材文件存在且非空",
                "最终产物通过独立 Asset Critic",
            ],
            budget=TaskBudget(
                max_steps=max(
                    2,
                    len(editorial.editorial_plan.visual_requirements),
                ),
                max_retries=0,
            ),
            input_hash=self._input_hash(editorial, voice),
            dependency_snapshot=graph.snapshot(visual_refs),
        )

    def create_revision_task(
        self,
        editorial: EditorialArtifact,
        voice: VoiceArtifact,
        state: ProjectState,
        current_artifact: AssetArtifact,
        evaluation: EvaluationResult,
    ) -> TaskEnvelope:
        """Regenerate only non-crop-repair failures from the last candidate."""
        task = self.create_task(editorial, voice, state)
        repairable = {
            "PREPARED_IMAGE_MISSING",
            "FOCUS_TARGET_TOO_SMALL",
            "TEXT_UNREADABLE",
            "LOW_RESOLUTION",
        }
        asset_to_visual = {
            asset.asset_id: asset.visual_request_id
            for asset in current_artifact.assets
        }
        visual_ids: set[str] = set()
        for issue in evaluation.issues:
            if issue.code in repairable:
                continue
            if issue.target_ref.startswith("asset:"):
                asset_id = issue.target_ref.split(":", 1)[1]
                if visual_id := asset_to_visual.get(asset_id):
                    visual_ids.add(visual_id)
            elif issue.target_ref.startswith("visual_requirement:"):
                visual_ids.add(issue.target_ref.split(":", 1)[1])
        if visual_ids:
            beat_by_visual = {
                item.visual_request_id: item.beat_id
                for item in editorial.editorial_plan.visual_requirements
            }
            task.task_id = (
                f"task_asset_revision_{editorial.project_id}_"
                f"v{state.video.state_version}"
            )
            task.goal = "Regenerate only visual requirements with non-repairable failures."
            task.scope.visual_request_ids = sorted(visual_ids)
            task.scope.beat_ids = [
                beat_by_visual[item] for item in sorted(visual_ids)
            ]
        return task

    def run(
        self,
        editorial: EditorialArtifact,
        voice: VoiceArtifact,
        project_dir: str | Path,
        state: ProjectState,
        task: TaskEnvelope | None = None,
        current_artifact: AssetArtifact | None = None,
    ) -> AssetHarnessRun:
        project_id = editorial.project_id
        if voice.project_id != project_id or state.video.project_id != project_id:
            raise ValueError("asset inputs have different project_id values")
        if state.video.editorial_status != "passed":
            raise ValueError("asset_agent requires an approved editorial artifact")
        is_repair = bool(task and task.scope.target_refs)
        if state.video.asset_status not in {"ready", "needs_revision", "stale"} and not (
            is_repair and state.video.asset_status == "passed"
        ):
            raise ValueError("asset_agent is not ready in project state")
        envelope = task or self.create_task(editorial, voice, state)
        if envelope.based_on_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: asset task uses an obsolete state version")
        expected_hash = (
            repair_input_hash(envelope.dependency_snapshot, envelope.scope.target_refs)
            if envelope.scope.target_refs
            else self._input_hash(editorial, voice)
        )
        if envelope.input_hash != expected_hash:
            raise ValueError("asset task input_hash does not match inputs")
        graph = DependencyGraph(state.dependency_graph)
        graph.validate_snapshot(envelope.dependency_snapshot)
        execution = self.agent.run(
            envelope,
            editorial=editorial,
            voice=voice,
            project_dir=project_dir,
            current_artifact=current_artifact,
        )
        if execution.result.status != "completed" or execution.candidate is None:
            raise RuntimeError(execution.result.error or "asset agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation, inspections = self.critic.evaluate(
            project_dir,
            execution.candidate.resolutions,
            execution.candidate.assets,
            [
                item.visual_request_id
                for item in editorial.editorial_plan.visual_requirements
            ],
            target_ref,
            voice,
            editorial,
            execution.candidate.prepared_images,
        )
        artifact = AssetArtifact(
            project_id=project_id,
            assets=execution.candidate.assets,
            resolutions=execution.candidate.resolutions,
            inspections=inspections,
            prepared_images=execution.candidate.prepared_images,
            quality=quality,
        )
        committed_version = state.video.state_version + 1
        transition = transition_after_review(
            current_agent="asset_agent",
            evaluation=evaluation,
            committed_state_version=committed_version,
            approved_target=(
                "repair_scheduler" if envelope.scope.target_refs else None
            ),
        )
        next_state = state.model_copy(deep=True)
        next_state.video.state_version = committed_version
        next_state.video.asset_status = (
            "passed" if evaluation.passed else "needs_revision"
        )
        next_state.video.timeline_status = (
            "ready" if evaluation.passed else "blocked"
        )
        next_graph = DependencyGraph(next_state.dependency_graph)
        commits = select_repair_commits(
            next_graph,
            asset_commits(artifact),
            envelope,
        )
        if evaluation.passed:
            graph_update = next_graph.commit_batch(
                task_id=envelope.task_id,
                produced_by="asset_agent",
                commits=commits,
            )
        else:
            graph_update = next_graph.reject_update(
                task_id=envelope.task_id,
                candidate_refs=[item.ref for item in commits],
                reason="Asset Critic rejected the candidate artifact",
            )
        record_task(
            next_state.trajectory,
            phase="asset",
            task_kind=(
                "repair"
                if envelope.scope.target_refs
                or AssetAgent.PREPARE_TOOL in envelope.allowed_tools
                else task_kind_for(state.trajectory, "asset")
            ),
            task=envelope,
            agent_result=execution.result,
            evaluation=evaluation,
            transition=transition,
            graph_update=graph_update,
        )
        next_state.runtime_context.available_tools = sorted(
            set(next_state.runtime_context.available_tools) | self.agent.tools.names
        )
        completed = AssetHarnessRun(
            artifact=artifact,
            record=AssetRunRecord(
                task=envelope,
                agent_result=execution.result,
                evaluation=evaluation,
                transition=transition,
                committed_state_version=committed_version,
                project_state=next_state,
            ),
        )
        repair_asset_ids = self._automatic_image_repair_ids(evaluation)
        if repair_asset_ids:
            repair_task = self._create_image_repair_task(
                editorial,
                voice,
                next_state,
                repair_asset_ids,
            )
            return self.run(
                editorial,
                voice,
                project_dir,
                next_state,
                repair_task,
                current_artifact=artifact,
            )
        return completed

    def _create_image_repair_task(
        self,
        editorial: EditorialArtifact,
        voice: VoiceArtifact,
        state: ProjectState,
        asset_ids: list[str],
    ) -> TaskEnvelope:
        visual_refs = [
            f"visual_requirement:{item.visual_request_id}"
            for item in editorial.editorial_plan.visual_requirements
        ]
        return TaskEnvelope(
            task_id=(
                f"task_asset_image_repair_{editorial.project_id}_"
                f"v{state.video.state_version}"
            ),
            agent="asset_agent",
            goal="Prepare only the rejected images for portrait video rendering.",
            scope=TaskScope(
                project_id=editorial.project_id,
                asset_ids=asset_ids,
            ),
            based_on_state_version=state.video.state_version,
            allowed_tools=[AssetAgent.PREPARE_TOOL],
            forbidden_actions=[
                "replace_source_asset",
                "modify_narrative",
                "modify_voice",
                "modify_editorial_plan",
            ],
            acceptance_criteria=[
                "Each scoped image has a non-empty 1080x1920 prepared output.",
                "The Asset Critic approves the final AssetArtifact.",
            ],
            budget=TaskBudget(
                max_steps=min(32, max(2, len(asset_ids))),
                max_retries=0,
            ),
            input_hash=self._input_hash(editorial, voice),
            dependency_snapshot=DependencyGraph(
                state.dependency_graph
            ).snapshot(visual_refs),
        )

    @staticmethod
    def _automatic_image_repair_ids(evaluation: EvaluationResult) -> list[str]:
        repairable = {
            "PREPARED_IMAGE_MISSING",
            "FOCUS_TARGET_TOO_SMALL",
            "TEXT_UNREADABLE",
            "LOW_RESOLUTION",
        }
        if evaluation.passed or not evaluation.issues:
            return []
        if any(issue.code not in repairable for issue in evaluation.issues):
            return []
        return sorted(
            {
                issue.target_ref.split(":", 1)[1]
                for issue in evaluation.issues
                if issue.target_ref.startswith("prepared_image:")
            }
        )

    @staticmethod
    def _input_hash(editorial: EditorialArtifact, voice: VoiceArtifact) -> str:
        payload = editorial.model_dump_json() + "\n" + voice.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_result(
        task: TaskEnvelope,
        result: AgentResult,
        state: ProjectState,
    ) -> None:
        if result.task_id != task.task_id or result.input_hash != task.input_hash:
            raise ValueError("asset agent result does not match task")
        if result.state_version_used != state.video.state_version:
            raise ValueError("STALE_RESULT: asset agent used an obsolete state")
        if set(result.state_patch.set) - {"video.asset_status"}:
            raise ValueError("asset agent proposed forbidden state paths")
        allowed = {"images:all", "timeline:all", "render:final"}
        if set(result.state_patch.invalidate) - allowed:
            raise ValueError("asset agent proposed invalid invalidations")
