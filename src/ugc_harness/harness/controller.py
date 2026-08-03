from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from ..agents.narrative_agent import NarrativeAgent
from ..evaluators.narrative_critic import NarrativeCritic
from ..agents.narrative_agent.models import CreativeBrief, NarrativeArtifact
from ..tools.registry import ToolRegistry
from .models import (
    AgentResult,
    EvaluationResult,
    ProjectState,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
    TrajectoryState,
    TransitionRecord,
    VideoState,
    RuntimeContext,
)
from .transitions import transition_after_review
from .dependencies import DependencyGraph
from .dependency_builders import narrative_commits
from .trajectory import record_task, task_kind_for
from .repair import repair_input_hash, select_repair_commits


class NarrativeRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    committed_state_version: int = Field(ge=0)
    project_state: ProjectState
    transition: TransitionRecord


class NarrativeHarnessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: NarrativeArtifact
    record: NarrativeRunRecord


class NarrativeHarnessController:
    """Controller boundary for the first domain-agent migration."""

    def __init__(
        self,
        agent: NarrativeAgent,
        critic: NarrativeCritic,
        model_name: str,
        *,
        state_version: int = 0,
    ) -> None:
        self.agent = agent
        self.critic = critic
        self.model_name = model_name
        self.state_version = state_version

    @classmethod
    def from_generator(
        cls,
        generator: object,
        model_name: str,
        *,
        state_version: int = 0,
    ) -> "NarrativeHarnessController":
        generate = getattr(generator, "generate", None)
        if not callable(generate):
            raise TypeError("generator must provide generate(prompt, output_type)")
        tools = ToolRegistry()
        tools.register(NarrativeAgent.PLAN_TOOL, generate)
        tools.register(NarrativeAgent.SCRIPT_TOOL, generate)
        return cls(
            NarrativeAgent(tools),
            NarrativeCritic(),
            model_name,
            state_version=state_version,
        )

    def create_task(self, brief: CreativeBrief) -> TaskEnvelope:
        input_hash = self._input_hash(brief)
        return TaskEnvelope(
            task_id=f"task_narrative_{brief.project_id}_v{self.state_version}",
            agent="narrative_agent",
            goal="生成可验收的 Section、PlannedBeat 与口播 Script",
            scope=TaskScope(project_id=brief.project_id),
            based_on_state_version=self.state_version,
            allowed_tools=[
                NarrativeAgent.PLAN_TOOL,
                NarrativeAgent.SCRIPT_TOOL,
            ],
            forbidden_actions=[
                "modify_voice",
                "modify_visuals",
                "modify_assets",
                "modify_timeline",
                "render_video",
            ],
            acceptance_criteria=[
                "Section 顺序为 hook、body、close",
                "每个 PlannedBeat 均有口播覆盖",
                "Close 兑现 Hook",
                "输出通过 Pydantic Schema 和独立 Narrative Critic",
            ],
            budget=TaskBudget(
                max_steps=4,
                max_retries=1,
                fallback_policy="use_best_available",
            ),
            input_hash=input_hash,
        )

    def run(
        self,
        brief: CreativeBrief,
        task: TaskEnvelope | None = None,
        state: ProjectState | None = None,
    ) -> NarrativeHarnessRun:
        if state is not None:
            if state.video.project_id != brief.project_id:
                raise ValueError("project state and brief have different project_id")
            self.state_version = state.video.state_version
        envelope = task or self.create_task(brief)
        if envelope.scope.project_id != brief.project_id:
            raise ValueError("task scope does not match brief project_id")
        if envelope.based_on_state_version != self.state_version:
            raise ValueError(
                "STALE_RESULT: task is based on state version "
                f"{envelope.based_on_state_version}, current is {self.state_version}"
            )
        expected_input_hash = (
            repair_input_hash(
                envelope.dependency_snapshot,
                envelope.scope.target_refs,
            )
            if envelope.scope.target_refs
            else self._input_hash(brief)
        )
        if envelope.input_hash != expected_input_hash:
            raise ValueError("task input_hash does not match the CreativeBrief")
        if state is not None:
            DependencyGraph(state.dependency_graph).validate_snapshot(
                envelope.dependency_snapshot
            )
        execution = self.agent.run(envelope, brief=brief)
        if (
            execution.result.status != "completed"
            or execution.planning is None
            or execution.script is None
        ):
            raise RuntimeError(
                execution.result.error or "narrative agent did not complete"
            )
        self._validate_result(envelope, execution.result)
        if state is not None:
            DependencyGraph(state.dependency_graph).validate_snapshot(
                envelope.dependency_snapshot
            )
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation = self.critic.evaluate(
            brief,
            execution.planning,
            execution.script,
            target_ref,
        )
        artifact = NarrativeArtifact(
            model=self.model_name,
            brief=brief,
            planning=execution.planning,
            script=execution.script,
            quality=quality,
        )
        # Commit is controller-owned. The agent only proposed a StatePatch.
        committed_version = self.state_version + 1
        transition = transition_after_review(
            current_agent="narrative_agent",
            evaluation=evaluation,
            committed_state_version=committed_version,
            approved_target=(
                "repair_scheduler" if envelope.scope.target_refs else None
            ),
        )
        narrative_status = "passed" if evaluation.passed else "needs_revision"
        successor_status = "ready" if evaluation.passed else "blocked"
        if state is None:
            project_state = ProjectState(
                runtime_context=RuntimeContext(
                    available_models={"llm": [self.model_name]},
                    available_tools=sorted(self.agent.tools.names),
                    constraints={
                        "aspect_ratio": brief.target.aspect_ratio,
                        "language": brief.target.language,
                    },
                ),
                world_state=execution.planning.world_state,
                video_profile=execution.planning.video_profile,
                video=VideoState(
                    project_id=brief.project_id,
                    state_version=committed_version,
                    narrative_status=narrative_status,
                    script_status=narrative_status,
                    voice_status=successor_status,
                    editorial_status="pending",
                    timeline_status="pending",
                    render_status="pending",
                ),
                trajectory=TrajectoryState(),
            )
        else:
            project_state = state.model_copy(deep=True)
            project_state.video.state_version = committed_version
            project_state.video.narrative_status = narrative_status
            project_state.video.script_status = narrative_status
            project_state.video.voice_status = successor_status
            project_state.video.editorial_status = "blocked"
            project_state.video.asset_status = "blocked"
            project_state.video.timeline_status = "blocked"
            project_state.video.render_status = "blocked"
            if evaluation.passed:
                project_state.world_state = execution.planning.world_state
                project_state.video_profile = execution.planning.video_profile
            project_state.runtime_context.available_tools = sorted(
                set(project_state.runtime_context.available_tools)
                | self.agent.tools.names
            )
            models = project_state.runtime_context.available_models.setdefault(
                "llm", []
            )
            if self.model_name not in models:
                models.append(self.model_name)
        graph = DependencyGraph(project_state.dependency_graph)
        commits = narrative_commits(artifact)
        commits = select_repair_commits(graph, commits, envelope)
        if evaluation.passed:
            graph_update = graph.commit_batch(
                task_id=envelope.task_id,
                produced_by="narrative_agent",
                commits=commits,
            )
        else:
            graph_update = graph.reject_update(
                task_id=envelope.task_id,
                candidate_refs=[item.ref for item in commits],
                reason="Narrative Critic rejected the candidate artifact",
            )
        record_task(
            project_state.trajectory,
            phase="narrative",
            task_kind=(
                "repair"
                if envelope.scope.target_refs
                else task_kind_for(project_state.trajectory, "narrative")
            ),
            task=envelope,
            agent_result=execution.result,
            evaluation=evaluation,
            transition=transition,
            graph_update=graph_update,
        )
        run = NarrativeHarnessRun(
            artifact=artifact,
            record=NarrativeRunRecord(
                task=envelope,
                agent_result=execution.result,
                evaluation=evaluation,
                committed_state_version=committed_version,
                project_state=project_state,
                transition=transition,
            ),
        )
        self.state_version = committed_version
        return run

    @staticmethod
    def _input_hash(brief: CreativeBrief) -> str:
        return hashlib.sha256(
            brief.model_dump_json().encode("utf-8")
        ).hexdigest()

    def _validate_result(
        self,
        task: TaskEnvelope,
        result: AgentResult,
    ) -> None:
        if result.task_id != task.task_id:
            raise ValueError("agent result task_id does not match task")
        if result.input_hash != task.input_hash:
            raise ValueError("agent result input_hash does not match task")
        if result.state_version_used != self.state_version:
            raise ValueError("STALE_RESULT: agent used an obsolete state version")
        allowed_set_paths = {
            "video.narrative_status",
            "video.script_status",
        }
        unexpected = set(result.state_patch.set) - allowed_set_paths
        if unexpected:
            raise ValueError(
                f"narrative agent proposed forbidden state paths: {sorted(unexpected)}"
            )
        allowed_invalidations = {
            "voice:all",
            "editorial:all",
            "timeline:all",
            "render:final",
        }
        unexpected_invalidations = (
            set(result.state_patch.invalidate) - allowed_invalidations
        )
        if unexpected_invalidations:
            raise ValueError(
                "narrative agent proposed invalid invalidations: "
                f"{sorted(unexpected_invalidations)}"
            )
