from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..agents.narrative_agent.models import NarrativeArtifact
from ..agents.voice_agent import VoiceAgent, VoiceArtifact
from ..agents.voice_agent.capabilities import TTSProvider, VoiceCapabilities
from ..agents.voice_agent.planning import build_voice_plan
from ..evaluators.voice_critic import VoiceCritic
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
from .dependency_builders import narrative_commits, voice_commits
from .trajectory import record_task, task_kind_for
from .repair import repair_input_hash, select_repair_commits


class VoiceRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    transition: TransitionRecord
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class VoiceHarnessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: VoiceArtifact
    record: VoiceRunRecord


class VoiceHarnessController:
    def __init__(self, agent: VoiceAgent, critic: VoiceCritic) -> None:
        self.agent = agent
        self.critic = critic

    @classmethod
    def from_provider(
        cls,
        provider: TTSProvider,
        voice_id: str,
    ) -> "VoiceHarnessController":
        capabilities = VoiceCapabilities(provider)
        tools = ToolRegistry()
        tools.register(VoiceAgent.PLAN_TOOL, build_voice_plan)
        tools.register(
            VoiceAgent.SYNTHESIZE_TOOL,
            capabilities.synthesize_narration,
        )
        return cls(VoiceAgent(tools, voice_id), VoiceCritic())

    def create_task(
        self,
        narrative: NarrativeArtifact,
        state: ProjectState,
    ) -> TaskEnvelope:
        graph = DependencyGraph(state.dependency_graph)
        return TaskEnvelope(
            task_id=(
                f"task_voice_{narrative.brief.project_id}_v"
                f"{state.video.state_version}"
            ),
            agent="voice_agent",
            goal="生成完整配音、原生字级时间戳与 RealizedBeat",
            scope=TaskScope(project_id=narrative.brief.project_id),
            based_on_state_version=state.video.state_version,
            allowed_tools=[VoiceAgent.PLAN_TOOL, VoiceAgent.SYNTHESIZE_TOOL],
            forbidden_actions=[
                "modify_narrative",
                "modify_visuals",
                "modify_assets",
                "modify_timeline",
            ],
            acceptance_criteria=[
                "所有 ScriptSegment 均有音频",
                "旁白 WAV 存在且非空",
                "所有 PlannedBeat 均形成 RealizedBeat",
                "输出通过独立 Voice Critic",
            ],
            budget=TaskBudget(max_steps=2, max_retries=0),
            input_hash=self._input_hash(narrative),
            dependency_snapshot=graph.snapshot(["artifact:narrative"]),
        )

    def run(
        self,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        state: ProjectState,
        task: TaskEnvelope | None = None,
    ) -> VoiceHarnessRun:
        if state.video.project_id != narrative.brief.project_id:
            raise ValueError("project state and narrative have different project_id")
        if state.video.narrative_status != "passed":
            raise ValueError("voice_agent requires an approved narrative artifact")
        is_repair = bool(task and task.scope.target_refs)
        if state.video.voice_status not in {"ready", "needs_revision", "stale"} and not (
            is_repair and state.video.voice_status == "passed"
        ):
            raise ValueError("voice_agent is not ready in project state")
        graph = DependencyGraph(state.dependency_graph)
        if "artifact:narrative" not in state.dependency_graph.nodes:
            graph.commit_batch(
                task_id=f"bootstrap_narrative_{narrative.brief.project_id}",
                produced_by="narrative_agent",
                commits=narrative_commits(narrative),
            )
        envelope = task or self.create_task(narrative, state)
        if envelope.based_on_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: voice task uses an obsolete state version")
        expected_input_hash = (
            repair_input_hash(
                envelope.dependency_snapshot,
                envelope.scope.target_refs,
            )
            if envelope.scope.target_refs
            else self._input_hash(narrative)
        )
        if envelope.input_hash != expected_input_hash:
            raise ValueError("voice task input_hash does not match narrative")
        graph.validate_snapshot(envelope.dependency_snapshot)
        execution = self.agent.run(
            envelope,
            narrative=narrative,
            project_dir=project_dir,
        )
        if execution.result.status != "completed" or execution.artifact is None:
            raise RuntimeError(execution.result.error or "voice agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        evaluation = self.critic.evaluate(
            execution.artifact,
            narrative,
            project_dir,
            target_ref,
        )
        committed_version = state.video.state_version + 1
        transition = transition_after_review(
            current_agent="voice_agent",
            evaluation=evaluation,
            committed_state_version=committed_version,
            approved_target=(
                "repair_scheduler" if envelope.scope.target_refs else None
            ),
        )
        next_state = state.model_copy(deep=True)
        next_state.video.state_version = committed_version
        next_state.video.voice_status = (
            "passed" if evaluation.passed else "needs_revision"
        )
        next_state.video.editorial_status = (
            "ready" if evaluation.passed else "blocked"
        )
        next_graph = DependencyGraph(next_state.dependency_graph)
        commits = voice_commits(execution.artifact)
        commits = select_repair_commits(next_graph, commits, envelope)
        if evaluation.passed:
            graph_update = next_graph.commit_batch(
                task_id=envelope.task_id,
                produced_by="voice_agent",
                commits=commits,
            )
        else:
            graph_update = next_graph.reject_update(
                task_id=envelope.task_id,
                candidate_refs=[item.ref for item in commits],
                reason="Voice Critic rejected the candidate artifact",
            )
        record_task(
            next_state.trajectory,
            phase="voice",
            task_kind=(
                "repair"
                if envelope.scope.target_refs
                else task_kind_for(state.trajectory, "voice")
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
        next_state.runtime_context.available_models["tts"] = [self.agent.voice_id]
        return VoiceHarnessRun(
            artifact=execution.artifact,
            record=VoiceRunRecord(
                task=envelope,
                agent_result=execution.result,
                evaluation=evaluation,
                transition=transition,
                committed_state_version=committed_version,
                project_state=next_state,
            ),
        )

    @staticmethod
    def _input_hash(narrative: NarrativeArtifact) -> str:
        return hashlib.sha256(
            narrative.model_dump_json().encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _validate_result(
        task: TaskEnvelope,
        result: AgentResult,
        state: ProjectState,
    ) -> None:
        if result.task_id != task.task_id or result.input_hash != task.input_hash:
            raise ValueError("voice agent result does not match task")
        if result.state_version_used != state.video.state_version:
            raise ValueError("STALE_RESULT: voice agent used an obsolete state version")
        if set(result.state_patch.set) - {"video.voice_status"}:
            raise ValueError("voice agent proposed forbidden state paths")
        allowed = {"editorial:all", "timeline:all", "render:final"}
        if set(result.state_patch.invalidate) - allowed:
            raise ValueError("voice agent proposed invalid invalidations")
