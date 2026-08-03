from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from ..agents.editorial_agent import EditorialAgent, EditorialArtifact
from ..agents.narrative_agent.models import NarrativeArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..evaluators.editorial_critic import EditorialCritic
from ..tools.registry import ToolRegistry
from .models import (
    AgentResult,
    EvaluationResult,
    ProjectState,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
    TransitionRecord,
)
from .transitions import transition_after_review
from .dependencies import DependencyGraph
from .dependency_builders import editorial_commits
from .trajectory import record_task, task_kind_for
from .repair import repair_input_hash, select_repair_commits


class EditorialRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    transition: TransitionRecord
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class EditorialHarnessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: EditorialArtifact
    record: EditorialRunRecord


class EditorialHarnessController:
    def __init__(
        self,
        agent: EditorialAgent,
        critic: EditorialCritic,
        model_name: str,
    ) -> None:
        self.agent = agent
        self.critic = critic
        self.model_name = model_name

    @classmethod
    def from_generator(
        cls,
        generator: object,
        model_name: str,
    ) -> "EditorialHarnessController":
        generate = getattr(generator, "generate", None)
        if not callable(generate):
            raise TypeError("generator must provide generate(prompt, output_type)")
        tools = ToolRegistry()
        tools.register(EditorialAgent.PLAN_TOOL, generate)
        return cls(EditorialAgent(tools), EditorialCritic(), model_name)

    def create_task(
        self,
        narrative: NarrativeArtifact,
        voice: VoiceArtifact,
        state: ProjectState,
    ) -> TaskEnvelope:
        graph = DependencyGraph(state.dependency_graph)
        dependency_refs = ["profile:video"] + [
            f"realized_beat:{beat.beat_id}" for beat in voice.realized_beats
        ]
        return TaskEnvelope(
            task_id=(
                f"task_editorial_{narrative.brief.project_id}_v"
                f"{state.video.state_version}"
            ),
            agent="editorial_agent",
            goal="生成主张映射和逐 Beat 的 A-roll/B-roll 视觉需求",
            scope=TaskScope(project_id=narrative.brief.project_id),
            based_on_state_version=state.video.state_version,
            allowed_tools=[EditorialAgent.PLAN_TOOL],
            forbidden_actions=[
                "modify_narrative",
                "modify_voice",
                "acquire_assets",
                "modify_timeline",
                "render_video",
            ],
            acceptance_criteria=[
                "每个 RealizedBeat 恰好有一个 VisualRequirement",
                "事实证据画面遵循来源约束",
                "完整保留已批准的 video_profile",
                "人物出镜比例和 character_id 满足 profile",
                "最终产物通过独立 Editorial Critic",
            ],
            budget=TaskBudget(
                max_steps=2,
                max_retries=2,
                fallback_policy="use_best_available",
            ),
            input_hash=self._input_hash(narrative, voice),
            dependency_snapshot=graph.snapshot(dependency_refs),
        )

    def run(
        self,
        narrative: NarrativeArtifact,
        voice: VoiceArtifact,
        state: ProjectState,
        task: TaskEnvelope | None = None,
        current_artifact: EditorialArtifact | None = None,
        critic_problems: list[str] | None = None,
        _attempt: int = 1,
    ) -> EditorialHarnessRun:
        project_id = narrative.brief.project_id
        if voice.project_id != project_id or state.video.project_id != project_id:
            raise ValueError("editorial inputs have different project_id values")
        if state.video.voice_status != "passed":
            raise ValueError("editorial_agent requires an approved voice artifact")
        is_repair = bool(task and task.scope.target_refs)
        if state.video.editorial_status not in {
            "ready",
            "needs_revision",
            "stale",
        } and not (is_repair and state.video.editorial_status == "passed"):
            raise ValueError("editorial_agent is not ready in project state")
        envelope = task or self.create_task(narrative, voice, state)
        if envelope.based_on_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: editorial task uses an obsolete state version")
        expected_input_hash = (
            repair_input_hash(
                envelope.dependency_snapshot,
                envelope.scope.target_refs,
            )
            if envelope.scope.target_refs
            else self._input_hash(narrative, voice)
        )
        if envelope.input_hash != expected_input_hash:
            raise ValueError("editorial task input_hash does not match inputs")
        DependencyGraph(state.dependency_graph).validate_snapshot(
            envelope.dependency_snapshot
        )

        execution = self.agent.run(
            envelope,
            narrative=narrative,
            voice=voice,
            current_artifact=current_artifact,
            critic_problems=critic_problems,
        )
        if execution.result.status != "completed" or execution.plan is None:
            raise RuntimeError(
                execution.result.error or "editorial agent did not complete"
            )
        self._validate_result(envelope, execution.result, state)
        DependencyGraph(state.dependency_graph).validate_snapshot(
            envelope.dependency_snapshot
        )
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation = self.critic.evaluate(
            narrative,
            voice,
            execution.plan,
            target_ref,
        )
        use_best_available = not evaluation.passed and _attempt >= 3
        if use_best_available:
            quality = quality.model_copy(
                update={
                    "passed": True,
                    "issues": [
                        *quality.issues,
                        "Editorial revision budget exhausted; using the third candidate.",
                    ],
                }
            )
            evaluation = EvaluationResult(
                critic_id=evaluation.critic_id,
                target_ref=evaluation.target_ref,
                passed=True,
                issues=[
                    issue.model_copy(
                        update={
                            "severity": "warning",
                            "diagnosis": (
                                f"{issue.diagnosis} "
                                "Third candidate retained by use_best_available."
                            ),
                        }
                    )
                    for issue in evaluation.issues
                ],
            )
        artifact = EditorialArtifact(
            model=self.model_name,
            project_id=project_id,
            editorial_plan=execution.plan,
            quality=quality,
        )
        committed_version = state.video.state_version + 1
        transition = (
            TransitionRecord(
                transition_id=(
                    f"transition_editorial_agent_{committed_version}_fallback"
                ),
                from_agent="editorial_agent",
                to_agent="asset_agent",
                outcome="advance",
                trigger_ref=evaluation.target_ref,
                reason=(
                    "Editorial revision budget exhausted after three candidates; "
                    "the third candidate was accepted by use_best_available"
                ),
                committed_state_version=committed_version,
            )
            if use_best_available
            else transition_after_review(
                current_agent="editorial_agent",
                evaluation=evaluation,
                committed_state_version=committed_version,
                approved_target=(
                    "repair_scheduler" if envelope.scope.target_refs else None
                ),
            )
        )
        next_state = state.model_copy(deep=True)
        next_state.video.state_version = committed_version
        next_state.video.editorial_status = (
            "passed" if evaluation.passed else "needs_revision"
        )
        next_state.video.asset_status = "ready" if evaluation.passed else "blocked"
        next_graph = DependencyGraph(next_state.dependency_graph)
        commits = editorial_commits(artifact)
        commits = select_repair_commits(next_graph, commits, envelope)
        if evaluation.passed:
            graph_update = next_graph.commit_batch(
                task_id=envelope.task_id,
                produced_by="editorial_agent",
                commits=commits,
            )
        else:
            graph_update = next_graph.reject_update(
                task_id=envelope.task_id,
                candidate_refs=[item.ref for item in commits],
                reason="Editorial Critic rejected the candidate artifact",
            )
        record_task(
            next_state.trajectory,
            phase="editorial",
            task_kind=(
                "repair"
                if envelope.scope.target_refs
                else task_kind_for(state.trajectory, "editorial")
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
        models = next_state.runtime_context.available_models.setdefault("llm", [])
        if self.model_name not in models:
            models.append(self.model_name)
        completed = EditorialHarnessRun(
            artifact=artifact,
            record=EditorialRunRecord(
                task=envelope,
                agent_result=execution.result,
                evaluation=evaluation,
                transition=transition,
                committed_state_version=committed_version,
                project_state=next_state,
            ),
        )
        if not evaluation.passed and _attempt < 3 and not envelope.scope.target_refs:
            return self.run(
                narrative,
                voice,
                next_state,
                current_artifact=artifact,
                critic_problems=[
                    issue.diagnosis for issue in evaluation.issues
                ],
                _attempt=_attempt + 1,
            )
        return completed

    @staticmethod
    def _input_hash(
        narrative: NarrativeArtifact,
        voice: VoiceArtifact,
    ) -> str:
        payload = narrative.model_dump_json() + "\n" + voice.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_result(
        task: TaskEnvelope,
        result: AgentResult,
        state: ProjectState,
    ) -> None:
        if result.task_id != task.task_id or result.input_hash != task.input_hash:
            raise ValueError("editorial agent result does not match task")
        if result.state_version_used != state.video.state_version:
            raise ValueError("STALE_RESULT: editorial agent used an obsolete state")
        if set(result.state_patch.set) - {"video.editorial_status"}:
            raise ValueError("editorial agent proposed forbidden state paths")
        allowed = {"assets:all", "timeline:all", "render:final"}
        if set(result.state_patch.invalidate) - allowed:
            raise ValueError("editorial agent proposed invalid invalidations")
