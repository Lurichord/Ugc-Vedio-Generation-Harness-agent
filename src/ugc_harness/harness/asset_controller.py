from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from typing import Any

from ..agents.asset_agent import AssetArtifact, AssetCandidate
from ..agents.asset_agent.capabilities import AssetCapabilities, AssetProvider
from ..agents.asset_agent.image_analysis import BasicImageAnalyzer
from ..agents.asset_agent.image_models import AssetInspection, PreparedImage
from ..agents.asset_agent.image_tools import ImagePreparationCapabilities
from ..agents.asset_agent.models import (
    AssetCard,
    VisualResolution,
    is_talking_head_video,
)
from ..agents.editorial_agent.models import EditorialArtifact
from ..agents.generic import (
    CompletionSpec,
    EnvironmentToolModel,
    GenericAgent,
    RegistryTool,
    RegistryToolTransport,
)
from ..agents.instructions import load_instructions
from ..agents.voice_agent.models import VoiceArtifact
from ..evaluators.asset_critic import AssetCritic, ImageAnalyzer
from ..tools.registry import ToolRegistry
from .dependencies import DependencyGraph
from .dependency_builders import asset_commits
from .models import (
    AgentResult,
    ArtifactRef,
    EvaluationResult,
    ProjectState,
    StatePatch,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
    TransitionRecord,
)
from .repair import repair_input_hash, select_repair_commits
from .trajectory import record_task, task_kind_for
from .transitions import transition_after_review


ACQUIRE_TOOL = "asset.acquire_requirement"
PREPARE_TOOL = "asset.prepare_image"
SUBMIT_TOOL = "asset.submit_candidate"


def _is_prepare_task(task: TaskEnvelope) -> bool:
    return ACQUIRE_TOOL not in task.allowed_tools or bool(
        task.scope.target_refs
        and all(
            ref.startswith("prepared_image:") for ref in task.scope.target_refs
        )
    )


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
    def __init__(self, agent: GenericAgent, critic: AssetCritic) -> None:
        self.agent = agent
        self.critic = critic

    @classmethod
    def from_provider(
        cls,
        provider: AssetProvider,
        image_analyzer: ImageAnalyzer | None = None,
        tool_model: object | None = None,
    ) -> "AssetHarnessController":
        acquire_capability = AssetCapabilities(provider).acquire_requirement
        prepare_capability = ImagePreparationCapabilities().prepare_image
        tools = ToolRegistry()
        for tool_name in (ACQUIRE_TOOL, PREPARE_TOOL, SUBMIT_TOOL):
            tools.register(tool_name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> RegistryToolTransport:
            editorial = kwargs["editorial"]
            voice = kwargs["voice"]
            project_dir = kwargs["project_dir"]
            current = kwargs.get("current_artifact")
            assert isinstance(editorial, EditorialArtifact)
            assert isinstance(voice, VoiceArtifact)
            if current is not None and not isinstance(current, AssetArtifact):
                raise TypeError("current_artifact must be an AssetArtifact")
            session = _AssetSession(
                task=task,
                editorial=editorial,
                voice=voice,
                project_dir=project_dir,
                current=current,
                acquire_capability=acquire_capability,
                prepare_capability=prepare_capability,
            )
            return RegistryToolTransport(
                [
                    RegistryTool(
                        name=ACQUIRE_TOOL,
                        description=(
                            "处理下一个待办 VisualRequirement：按 first-success "
                            "规则获取素材并给出 resolution；返回剩余待办清单。"
                        ),
                        handler=session.acquire_next,
                    ),
                    RegistryTool(
                        name=PREPARE_TOOL,
                        description=(
                            "图像修复模式：处理下一个待办 asset_id，产出 1080x1920 "
                            "的竖屏预处理图；返回剩余待办清单。"
                        ),
                        handler=session.prepare_next,
                    ),
                    RegistryTool(
                        name=SUBMIT_TOOL,
                        description="待办清空后提交素材候选；未清空时返回可修复错误。",
                        handler=session.submit,
                    ),
                ]
            )

        return cls(
            GenericAgent(
                tool_model=tool_model or EnvironmentToolModel(),  # type: ignore[arg-type]
                transport_factory=transport_factory,
                candidate_type=AssetCandidate,
                completion_builder=_asset_completion,
                capability_tools=tools,
            ),
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
            agent_instructions=load_instructions("asset"),
            allowed_tools=[ACQUIRE_TOOL, SUBMIT_TOOL],
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
                max_steps=min(
                    32,
                    len(editorial.editorial_plan.visual_requirements) + 2,
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
            elif issue.target_ref.startswith("visual_resolution:"):
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
        candidate = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            candidate, AssetCandidate
        ):
            raise RuntimeError(execution.result.error or "asset agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation, inspections = self.critic.evaluate(
            project_dir,
            candidate.resolutions,
            candidate.assets,
            [
                item.visual_request_id
                for item in editorial.editorial_plan.visual_requirements
            ],
            target_ref,
            voice,
            editorial,
            candidate.prepared_images,
        )
        artifact = AssetArtifact(
            project_id=project_id,
            assets=candidate.assets,
            resolutions=candidate.resolutions,
            inspections=inspections,
            prepared_images=candidate.prepared_images,
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
                or ACQUIRE_TOOL not in envelope.allowed_tools
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
            agent_instructions=load_instructions("asset"),
            allowed_tools=[PREPARE_TOOL, SUBMIT_TOOL],
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
                max_steps=min(32, len(asset_ids) + 2),
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


class _AssetSession:
    """Task-local cursor over pending visual requirements or image repairs.

    Sequencing and continuity grouping are data preparation, not decisions,
    so they live here; the model only decides continue / retry / submit.
    """

    def __init__(
        self,
        *,
        task: TaskEnvelope,
        editorial: EditorialArtifact,
        voice: VoiceArtifact,
        project_dir: object,
        current: AssetArtifact | None,
        acquire_capability: Any,
        prepare_capability: Any,
    ) -> None:
        self.task = task
        self.editorial = editorial
        self.project_dir = project_dir
        self.current = current
        self.acquire_capability = acquire_capability
        self.prepare_capability = prepare_capability
        self.prepare_mode = _is_prepare_task(task)
        self.beats = {beat.beat_id: beat for beat in voice.realized_beats}
        self.visuals = list(editorial.editorial_plan.visual_requirements)
        self.assets_by_request = {
            item.visual_request_id: item
            for item in (current.assets if current else [])
        }
        self.resolutions_by_request = {
            item.visual_request_id: item
            for item in (current.resolutions if current else [])
        }
        self.inspections_by_asset = {
            item.asset_id: item for item in (current.inspections if current else [])
        }
        self.prepared_by_asset = {
            item.asset_id: item
            for item in (current.prepared_images if current else [])
        }
        self.acquired_ids: list[str] = []
        self.prepared_ids: list[str] = []
        if self.prepare_mode:
            if current is None:
                raise ValueError("image repair requires the current AssetArtifact")
            if not task.scope.asset_ids:
                raise ValueError("image repair task has no asset_ids")
            self.pending: list[str] = sorted(set(task.scope.asset_ids))
        else:
            requested = set(task.scope.visual_request_ids)
            if task.scope.target_refs and not requested:
                raise ValueError("asset repair task has no visual_request_ids")
            target_visuals = [
                visual
                for visual in self.visuals
                if not requested or visual.visual_request_id in requested
            ]
            if requested - {item.visual_request_id for item in target_visuals}:
                raise ValueError("asset task references an unknown visual requirement")
            if task.scope.target_refs and current is None:
                raise ValueError("asset repair requires the current AssetArtifact")
            self.pending = [item.visual_request_id for item in target_visuals]
            self.group_by_request = self._continuity_groups(self.visuals)

    @staticmethod
    def _continuity_groups(visuals: list) -> dict[str, str]:
        groups: dict[str, str] = {}
        group_index = 0
        previous = None
        for visual in visuals:
            if visual.track == "a_roll":
                if not (
                    previous
                    and previous.track == "a_roll"
                    and previous.character_id == visual.character_id
                ):
                    group_index += 1
                groups[visual.visual_request_id] = (
                    f"{visual.character_id}_group_{group_index:02d}"
                )
            previous = visual
        return groups

    def acquire_next(self, problems: list[str] | None = None) -> dict[str, Any]:
        if self.prepare_mode:
            raise RuntimeError(
                "这是图像修复任务，请调用 asset.prepare_image"
            )
        if not self.pending:
            raise RuntimeError(
                "待办 VisualRequirement 已清空；请调用 asset.submit_candidate 提交"
            )
        visual_id = self.pending[0]
        visual = next(
            item for item in self.visuals if item.visual_request_id == visual_id
        )
        beat = self.beats.get(visual.beat_id)
        if beat is None:
            raise ValueError(
                f"VisualRequirement references unknown beat: {visual.beat_id}"
            )
        visual_index = self.visuals.index(visual)
        prior_visual = self.visuals[visual_index - 1] if visual_index else None
        adjacent_aroll = bool(
            prior_visual
            and prior_visual.track == "a_roll"
            and visual.track == "a_roll"
            and prior_visual.character_id == visual.character_id
        )
        previous_character_asset = (
            self.assets_by_request.get(prior_visual.visual_request_id)
            if adjacent_aroll and prior_visual
            else None
        )
        if previous_character_asset is not None and not is_talking_head_video(
            previous_character_asset
        ):
            # The plan said "adjacent a_roll", but the prior direction actually
            # fell back to a non-video asset. Break the continuity chain so the
            # provider restarts from the identity reference.
            previous_character_asset = None
        acquired = self.acquire_capability(
            project_id=self.editorial.project_id,
            visual=visual,
            beat=beat,
            project_dir=self.project_dir,
            character_description=(
                self.editorial.editorial_plan.video_profile.character_description
                if visual.track == "a_roll"
                else None
            ),
            continuity_group_id=getattr(self, "group_by_request", {}).get(visual_id),
            previous_character_asset=previous_character_asset,
        )
        if not (
            isinstance(acquired, tuple)
            and len(acquired) == 2
            and (acquired[0] is None or isinstance(acquired[0], AssetCard))
            and isinstance(acquired[1], VisualResolution)
        ):
            raise TypeError("asset tool returned an invalid acquisition result")
        asset, resolution = acquired
        self.assets_by_request.pop(visual_id, None)
        if current_asset := next(
            (
                item
                for item in (self.current.assets if self.current else [])
                if item.visual_request_id == visual_id
            ),
            None,
        ):
            self.inspections_by_asset.pop(current_asset.asset_id, None)
            self.prepared_by_asset.pop(current_asset.asset_id, None)
        if asset is not None:
            self.assets_by_request[visual_id] = asset
        self.resolutions_by_request[visual_id] = resolution
        self.pending.pop(0)
        self.acquired_ids.append(visual_id)
        return {
            "visual_request_id": visual_id,
            "resolved_with_asset": asset is not None,
            "acquired": list(self.acquired_ids),
            "pending": list(self.pending),
        }

    def prepare_next(self, problems: list[str] | None = None) -> dict[str, Any]:
        if not self.prepare_mode:
            raise RuntimeError(
                "当前是素材获取任务，请调用 asset.acquire_requirement"
            )
        if not self.pending:
            raise RuntimeError(
                "待修复图像已清空；请调用 asset.submit_candidate 提交"
            )
        assert self.current is not None
        asset_id = self.pending[0]
        asset = next(
            (item for item in self.current.assets if item.asset_id == asset_id),
            None,
        )
        inspection = self.inspections_by_asset.get(asset_id)
        if asset is None or inspection is None:
            raise ValueError(
                f"image repair is missing asset or inspection: {asset_id}"
            )
        result = self.prepare_capability(
            asset=asset,
            inspection=inspection,
            project_dir=self.project_dir,
        )
        if not isinstance(result, PreparedImage):
            raise TypeError("asset.prepare_image returned an invalid result")
        self.prepared_by_asset[asset_id] = result
        self.pending.pop(0)
        self.prepared_ids.append(asset_id)
        return {
            "asset_id": asset_id,
            "prepared": list(self.prepared_ids),
            "pending": list(self.pending),
        }

    def submit(self, problems: list[str] | None = None) -> AssetCandidate:
        if self.pending:
            raise RuntimeError(
                "候选不完整，仍有待办条目：" + "、".join(self.pending)
            )
        if self.prepare_mode:
            assert self.current is not None
            return AssetCandidate(
                assets=self.current.assets,
                resolutions=self.current.resolutions,
                inspections=self.current.inspections,
                prepared_images=list(self.prepared_by_asset.values()),
            )
        ordered_ids = [item.visual_request_id for item in self.visuals]
        return AssetCandidate(
            assets=[
                self.assets_by_request[ref]
                for ref in ordered_ids
                if ref in self.assets_by_request
            ],
            resolutions=[
                self.resolutions_by_request[ref]
                for ref in ordered_ids
                if ref in self.resolutions_by_request
            ],
            inspections=list(self.inspections_by_asset.values()),
            prepared_images=list(self.prepared_by_asset.values()),
        )


def _asset_completion(
    task: TaskEnvelope,
    kwargs: dict[str, Any],
) -> CompletionSpec:
    editorial = kwargs["editorial"]
    assert isinstance(editorial, EditorialArtifact)
    kind = "prepared_images" if _is_prepare_task(task) else "asset_candidate"
    return CompletionSpec(
        artifact_refs=[ArtifactRef(kind=kind, id=editorial.project_id)],
        state_patch=StatePatch(
            set={"video.asset_status": "ready"},
            invalidate=["timeline:all", "render:final"],
        ),
        evaluation_target=(
            f"assets:{editorial.project_id}@{task.based_on_state_version + 1}"
        ),
    )
