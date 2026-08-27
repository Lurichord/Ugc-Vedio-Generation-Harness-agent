from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from typing import Any

from ..agents.generic import (
    CompletionSpec,
    EnvironmentToolModel,
    GenericAgent,
    RegistryTool,
    RegistryToolTransport,
)
from ..agents.instructions import load_instructions
from ..agents.render_agent import RenderArtifact
from ..agents.render_agent.models import RenderCandidate
from ..agents.timeline_agent.models import TimelineArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..evaluators.render_critic import RenderCritic
from ..tools.registry import ToolRegistry
from .dependencies import DependencyGraph
from .dependency_builders import render_commits
from .description_realization import apply_render_realization
from .models import AgentResult, ArtifactRef, EvaluationResult, ProjectState, StatePatch, TaskBudget, TaskEnvelope, TaskScope, TransitionRecord
from .repair import repair_input_hash, select_repair_commits
from .trajectory import record_task, task_kind_for
from .transitions import transition_after_review


RENDER_TOOL = "render.execute"
SUBMIT_TOOL = "render.submit_candidate"


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
    def __init__(self, agent: GenericAgent, critic: RenderCritic) -> None:
        self.agent = agent
        self.critic = critic

    @classmethod
    def from_renderer(
        cls,
        renderer: Callable[..., RenderCandidate],
        tool_model: object | None = None,
    ) -> "RenderHarnessController":
        tools = ToolRegistry()
        for tool_name in (RENDER_TOOL, SUBMIT_TOOL):
            tools.register(tool_name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> RegistryToolTransport:
            voice = kwargs["voice"]
            timeline = kwargs["timeline"]
            project_dir = kwargs["project_dir"]
            assert isinstance(voice, VoiceArtifact)
            assert isinstance(timeline, TimelineArtifact)
            session: dict[str, Any] = {}

            def execute(problems: list[str] | None = None) -> RenderCandidate:
                candidate = renderer(
                    voice=voice,
                    timeline_artifact=timeline,
                    project_dir=project_dir,
                )
                if not isinstance(candidate, RenderCandidate):
                    raise TypeError("render.execute returned an invalid artifact")
                session["candidate"] = candidate
                return candidate

            def submit(problems: list[str] | None = None) -> RenderCandidate:
                candidate = session.get("candidate")
                if not isinstance(candidate, RenderCandidate):
                    raise RuntimeError(
                        "candidate is incomplete: nothing has been rendered yet"
                    )
                return candidate

            return RegistryToolTransport(
                [
                    RegistryTool(
                        name=RENDER_TOOL,
                        description=(
                            "把已批准的时间线渲染为最终与预览 MP4 并探测输出指标。"
                        ),
                        handler=execute,
                    ),
                    RegistryTool(
                        name=SUBMIT_TOOL,
                        description="渲染输出就绪后提交候选。",
                        handler=submit,
                    ),
                ]
            )

        return cls(
            GenericAgent(
                tool_model=tool_model or EnvironmentToolModel(),  # type: ignore[arg-type]
                transport_factory=transport_factory,
                candidate_type=RenderCandidate,
                completion_builder=_render_completion,
                capability_tools=tools,
            ),
            RenderCritic(),
        )

    def create_task(self, voice: VoiceArtifact, timeline: TimelineArtifact, state: ProjectState) -> TaskEnvelope:
        snapshots = DependencyGraph(state.dependency_graph).snapshot(["artifact:timeline"])
        return TaskEnvelope(
            task_id=f"task_render_{voice.project_id}_v{state.video.state_version}",
            agent="render_agent",
            goal="Render the approved timeline into final and preview MP4 outputs.",
            scope=TaskScope(project_id=voice.project_id, beat_ids=[item.beat_id for item in timeline.timeline.clips]),
            based_on_state_version=state.video.state_version,
            agent_instructions=load_instructions("render"),
            allowed_tools=[RENDER_TOOL, SUBMIT_TOOL],
            required_outputs=["render_candidate"],
            forbidden_actions=["modify_timeline", "modify_voice", "replace_assets"],
            acceptance_criteria=["Final output is 1080x1920 at 30fps.", "Final output contains audio and video streams.", "Duration differs from narration by no more than one frame.", "The Render Critic approves both outputs."],
            budget=TaskBudget(max_steps=4, max_retries=0),
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
        candidate = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            candidate, RenderCandidate
        ):
            raise RuntimeError(execution.result.error or "render agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation = self.critic.evaluate(project_dir, timeline, candidate, target_ref)
        artifact = RenderArtifact(
            **candidate.model_dump(),
            quality=quality,
        )
        committed_version = state.video.state_version + 1
        transition = transition_after_review(current_agent="render_agent", evaluation=evaluation, committed_state_version=committed_version, approved_target="repair_scheduler" if envelope.scope.target_refs else None)
        next_state = state.model_copy(deep=True)
        next_state.video.state_version = committed_version
        next_state.video.render_status = "passed" if evaluation.passed else "needs_revision"
        if evaluation.passed:
            apply_render_realization(next_state, artifact)
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


def _render_completion(
    task: TaskEnvelope,
    kwargs: dict[str, Any],
) -> CompletionSpec:
    voice = kwargs["voice"]
    assert isinstance(voice, VoiceArtifact)
    return CompletionSpec(
        artifact_refs=[ArtifactRef(kind="render_candidate", id=voice.project_id)],
        state_patch=StatePatch(set={"video.render_status": "ready"}),
        evaluation_target=(
            f"render:{voice.project_id}@{task.based_on_state_version + 1}"
        ),
    )
