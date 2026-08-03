from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from ..agents.render_agent import RenderAgent, RenderArtifact
from ..agents.render_agent.models import RenderCandidate
from ..agents.timeline_agent.models import TimelineArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..evaluators.render_critic import RenderCritic
from ..tools.registry import ToolRegistry
from .dependencies import DependencyGraph
from .dependency_builders import render_commits
from .models import AgentResult, EvaluationResult, ProjectState, TaskBudget, TaskEnvelope, TaskScope, TransitionRecord
from .repair import repair_input_hash, select_repair_commits
from .trajectory import record_task, task_kind_for
from .transitions import transition_after_review


class RenderRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    transition: TransitionRecord
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class RenderHarnessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: RenderArtifact
    record: RenderRunRecord


class RenderHarnessController:
    def __init__(self, agent: RenderAgent, critic: RenderCritic) -> None:
        self.agent = agent
        self.critic = critic

    @classmethod
    def from_renderer(cls, renderer: Callable[..., RenderCandidate]) -> "RenderHarnessController":
        tools = ToolRegistry()
        tools.register(RenderAgent.RENDER_TOOL, renderer)
        return cls(RenderAgent(tools), RenderCritic())

    def create_task(self, voice: VoiceArtifact, timeline: TimelineArtifact, state: ProjectState) -> TaskEnvelope:
        snapshots = DependencyGraph(state.dependency_graph).snapshot(["artifact:timeline"])
        return TaskEnvelope(
            task_id=f"task_render_{voice.project_id}_v{state.video.state_version}",
            agent="render_agent",
            goal="Render the approved timeline into final and preview MP4 outputs.",
            scope=TaskScope(project_id=voice.project_id, beat_ids=[item.beat_id for item in timeline.timeline.clips]),
            based_on_state_version=state.video.state_version,
            allowed_tools=[RenderAgent.RENDER_TOOL],
            forbidden_actions=["modify_timeline", "modify_voice", "replace_assets"],
            acceptance_criteria=["Final output is 1080x1920 at 30fps.", "Final output contains audio and video streams.", "Duration differs from narration by no more than one frame.", "The Render Critic approves both outputs."],
            budget=TaskBudget(max_steps=2, max_retries=0),
            input_hash=self._input_hash(voice, timeline),
            dependency_snapshot=snapshots,
        )

    def run(self, voice: VoiceArtifact, timeline: TimelineArtifact, project_dir: str | Path, state: ProjectState, task: TaskEnvelope | None = None) -> RenderHarnessRun:
        if timeline.project_id != voice.project_id or state.video.project_id != voice.project_id:
            raise ValueError("render inputs have different project_id values")
        if state.video.timeline_status != "passed":
            raise ValueError("render_agent requires an approved timeline artifact")
        is_repair = bool(task and task.scope.target_refs)
        if state.video.render_status not in {"ready", "needs_revision", "stale"} and not (is_repair and state.video.render_status == "passed"):
            raise ValueError("render_agent is not ready in project state")
        envelope = task or self.create_task(voice, timeline, state)
        if envelope.based_on_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: render task uses an obsolete state version")
        expected_hash = repair_input_hash(envelope.dependency_snapshot, envelope.scope.target_refs) if envelope.scope.target_refs else self._input_hash(voice, timeline)
        if envelope.input_hash != expected_hash:
            raise ValueError("render task input_hash does not match inputs")
        graph = DependencyGraph(state.dependency_graph)
        graph.validate_snapshot(envelope.dependency_snapshot)
        execution = self.agent.run(envelope, voice=voice, timeline=timeline, project_dir=project_dir)
        if execution.result.status != "completed" or execution.candidate is None:
            raise RuntimeError(execution.result.error or "render agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation = self.critic.evaluate(project_dir, timeline, execution.candidate, target_ref)
        artifact = RenderArtifact(
            **execution.candidate.model_dump(),
            quality=quality,
        )
        committed_version = state.video.state_version + 1
        transition = transition_after_review(current_agent="render_agent", evaluation=evaluation, committed_state_version=committed_version, approved_target="repair_scheduler" if envelope.scope.target_refs else None)
        next_state = state.model_copy(deep=True)
        next_state.video.state_version = committed_version
        next_state.video.render_status = "passed" if evaluation.passed else "needs_revision"
        next_graph = DependencyGraph(next_state.dependency_graph)
        commits = select_repair_commits(next_graph, render_commits(artifact), envelope)
        graph_update = next_graph.commit_batch(task_id=envelope.task_id, produced_by="render_agent", commits=commits) if evaluation.passed else next_graph.reject_update(task_id=envelope.task_id, candidate_refs=[item.ref for item in commits], reason="Render Critic rejected the candidate artifact")
        record_task(next_state.trajectory, phase="render", task_kind="repair" if envelope.scope.target_refs else task_kind_for(state.trajectory, "render"), task=envelope, agent_result=execution.result, evaluation=evaluation, transition=transition, graph_update=graph_update)
        next_state.runtime_context.available_tools = sorted(set(next_state.runtime_context.available_tools) | self.agent.tools.names)
        return RenderHarnessRun(artifact=artifact, record=RenderRunRecord(task=envelope, agent_result=execution.result, evaluation=evaluation, transition=transition, committed_state_version=committed_version, project_state=next_state))

    @staticmethod
    def _input_hash(voice: VoiceArtifact, timeline: TimelineArtifact) -> str:
        return hashlib.sha256((voice.model_dump_json() + "\n" + timeline.model_dump_json()).encode()).hexdigest()

    @staticmethod
    def _validate_result(task: TaskEnvelope, result: AgentResult, state: ProjectState) -> None:
        if result.task_id != task.task_id or result.input_hash != task.input_hash:
            raise ValueError("render agent result does not match task")
        if result.state_version_used != state.video.state_version:
            raise ValueError("STALE_RESULT: render agent used an obsolete state")
        if set(result.state_patch.set) - {"video.render_status"}:
            raise ValueError("render agent proposed forbidden state paths")
        if result.state_patch.invalidate:
            raise ValueError("render agent cannot invalidate upstream artifacts")
