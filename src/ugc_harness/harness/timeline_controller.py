from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from typing import Any

from ..agents.asset_agent.models import AssetArtifact
from ..agents.editorial_agent.models import EditorialArtifact
from ..agents.generic import (
    CompletionSpec,
    EnvironmentToolModel,
    GenericAgent,
    RegistryTool,
    RegistryToolTransport,
)
from ..agents.instructions import load_instructions
from ..agents.timeline_agent import TimelineArtifact
from ..agents.timeline_agent.capabilities import ScreenAnimationProvider, TimelineCapabilities
from ..agents.timeline_agent.models import TimelineCandidate
from ..agents.voice_agent.models import VoiceArtifact
from ..evaluators.timeline_critic import TimelineCritic
from ..tools.registry import ToolRegistry
from .dependencies import DependencyGraph
from .dependency_builders import timeline_commits
from .description_realization import apply_timeline_realization
from .models import AgentResult, ArtifactRef, EvaluationResult, ProjectState, StatePatch, TaskBudget, TaskEnvelope, TaskScope, TransitionRecord
from .repair import repair_input_hash, select_repair_commits
from .trajectory import record_task, task_kind_for
from .transitions import transition_after_review


COMPOSE_TOOL = "timeline.compose"
SUBMIT_TOOL = "timeline.submit_candidate"


class TimelineRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    transition: TransitionRecord
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class TimelineHarnessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: TimelineArtifact
    record: TimelineRunRecord


class TimelineHarnessController:
    def __init__(self, agent: GenericAgent, critic: TimelineCritic) -> None:
        self.agent = agent
        self.critic = critic

    @classmethod
    def from_provider(
        cls,
        provider: ScreenAnimationProvider,
        tool_model: object | None = None,
    ) -> "TimelineHarnessController":
        compose_capability = TimelineCapabilities(provider).run
        tools = ToolRegistry()
        for tool_name in (COMPOSE_TOOL, SUBMIT_TOOL):
            tools.register(tool_name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> RegistryToolTransport:
            voice = kwargs["voice"]
            editorial = kwargs["editorial"]
            assets = kwargs["assets"]
            project_dir = kwargs["project_dir"]
            current = kwargs.get("current_artifact")
            assert isinstance(voice, VoiceArtifact)
            assert isinstance(editorial, EditorialArtifact)
            assert isinstance(assets, AssetArtifact)
            if current is not None and not isinstance(current, TimelineArtifact):
                raise TypeError("current_artifact must be a TimelineArtifact")
            if task.scope.target_refs and current is None:
                raise ValueError(
                    "timeline repair requires the current TimelineArtifact"
                )
            session: dict[str, Any] = {}

            def compose(problems: list[str] | None = None) -> TimelineCandidate:
                candidate = compose_capability(
                    voice=voice,
                    editorial=editorial,
                    assets_artifact=assets,
                    project_dir=project_dir,
                )
                if not isinstance(candidate, TimelineCandidate):
                    raise TypeError("timeline.compose returned an invalid artifact")
                if task.scope.target_refs:
                    candidate = _merge_scoped(
                        candidate,
                        current,
                        set(task.scope.beat_ids),
                    )
                session["candidate"] = candidate
                return candidate

            def submit(problems: list[str] | None = None) -> TimelineCandidate:
                candidate = session.get("candidate")
                if not isinstance(candidate, TimelineCandidate):
                    raise RuntimeError(
                        "candidate is incomplete: the timeline has not been "
                        "composed yet"
                    )
                return candidate

            return RegistryToolTransport(
                [
                    RegistryTool(
                        name=COMPOSE_TOOL,
                        description=(
                            "把配音、编辑部计划和素材合成为以音频为时钟的时间线候选；"
                            "修复任务自动只合并 scope 内的 beat。"
                        ),
                        handler=compose,
                    ),
                    RegistryTool(
                        name=SUBMIT_TOOL,
                        description="时间线候选完整后提交。",
                        handler=submit,
                    ),
                ]
            )

        return cls(
            GenericAgent(
                tool_model=tool_model or EnvironmentToolModel(),  # type: ignore[arg-type]
                transport_factory=transport_factory,
                candidate_type=TimelineCandidate,
                completion_builder=_timeline_completion,
                capability_tools=tools,
            ),
            TimelineCritic(),
        )

    def create_task(self, voice: VoiceArtifact, editorial: EditorialArtifact, assets: AssetArtifact, state: ProjectState) -> TaskEnvelope:
        graph = DependencyGraph(state.dependency_graph)
        dependencies = ["artifact:voice", "artifact:editorial", "artifact:assets"]
        return TaskEnvelope(
            task_id=f"task_timeline_{voice.project_id}_v{state.video.state_version}",
            agent="timeline_agent",
            goal="Compose an audio-clocked clip, caption, transform, and overlay timeline.",
            scope=TaskScope(project_id=voice.project_id, beat_ids=[item.beat_id for item in voice.realized_beats]),
            based_on_state_version=state.video.state_version,
            agent_instructions=load_instructions("timeline"),
            allowed_tools=[COMPOSE_TOOL, SUBMIT_TOOL],
            required_outputs=["timeline_candidate"],
            forbidden_actions=["modify_voice", "modify_editorial_plan", "replace_assets", "render_video"],
            acceptance_criteria=["Every RealizedBeat has one contiguous clip.", "The timeline covers the complete narration audio.", "All playback media exists.", "The Timeline Critic approves the artifact."],
            budget=TaskBudget(max_steps=4, max_retries=0),
            input_hash=self._input_hash(voice, editorial, assets),
            dependency_snapshot=graph.snapshot(dependencies),
        )

    def run(self, voice: VoiceArtifact, editorial: EditorialArtifact, assets: AssetArtifact, project_dir: str | Path, state: ProjectState, task: TaskEnvelope | None = None, current_artifact: TimelineArtifact | None = None) -> TimelineHarnessRun:
        project_id = voice.project_id
        if {editorial.project_id, assets.project_id, state.video.project_id} != {project_id}:
            raise ValueError("timeline inputs have different project_id values")
        if state.video.asset_status != "passed":
            raise ValueError("timeline_agent requires an approved asset artifact")
        is_repair = bool(task and task.scope.target_refs)
        if state.video.timeline_status not in {"ready", "needs_revision", "stale"} and not (is_repair and state.video.timeline_status == "passed"):
            raise ValueError("timeline_agent is not ready in project state")
        input_hash = self._input_hash(voice, editorial, assets)
        envelope = task or self.create_task(voice, editorial, assets, state)
        if envelope.based_on_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: timeline task uses an obsolete state version")
        expected_hash = repair_input_hash(envelope.dependency_snapshot, envelope.scope.target_refs) if envelope.scope.target_refs else input_hash
        if envelope.input_hash != expected_hash:
            raise ValueError("timeline task input_hash does not match inputs")
        graph = DependencyGraph(state.dependency_graph)
        graph.validate_snapshot(envelope.dependency_snapshot)
        execution = self.agent.run(envelope, voice=voice, editorial=editorial, assets=assets, project_dir=project_dir, current_artifact=current_artifact)
        candidate = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            candidate, TimelineCandidate
        ):
            raise RuntimeError(execution.result.error or "timeline agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation = self.critic.evaluate(project_dir, voice, candidate, target_ref)
        artifact = TimelineArtifact(
            **candidate.model_dump(),
            quality=quality,
        )
        committed_version = state.video.state_version + 1
        transition = transition_after_review(current_agent="timeline_agent", evaluation=evaluation, committed_state_version=committed_version, approved_target="repair_scheduler" if envelope.scope.target_refs else None)
        next_state = state.model_copy(deep=True)
        next_state.video.state_version = committed_version
        next_state.video.timeline_status = "passed" if evaluation.passed else "needs_revision"
        next_state.video.render_status = "ready" if evaluation.passed else "blocked"
        if evaluation.passed:
            apply_timeline_realization(next_state, artifact)
        next_graph = DependencyGraph(next_state.dependency_graph)
        commits = select_repair_commits(next_graph, timeline_commits(artifact), envelope)
        graph_update = next_graph.commit_batch(task_id=envelope.task_id, produced_by="timeline_agent", commits=commits) if evaluation.passed else next_graph.reject_update(task_id=envelope.task_id, candidate_refs=[item.ref for item in commits], reason="Timeline Critic rejected the candidate artifact")
        record_task(next_state.trajectory, phase="timeline", task_kind="repair" if envelope.scope.target_refs else task_kind_for(state.trajectory, "timeline"), task=envelope, agent_result=execution.result, evaluation=evaluation, transition=transition, graph_update=graph_update)
        next_state.runtime_context.available_tools = sorted(set(next_state.runtime_context.available_tools) | self.agent.tools.names)
        return TimelineHarnessRun(artifact=artifact, record=TimelineRunRecord(task=envelope, agent_result=execution.result, evaluation=evaluation, transition=transition, committed_state_version=committed_version, project_state=next_state))

    @staticmethod
    def _input_hash(voice: VoiceArtifact, editorial: EditorialArtifact, assets: AssetArtifact) -> str:
        payload = "\n".join([voice.model_dump_json(), editorial.model_dump_json(), assets.model_dump_json()])
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _validate_result(task: TaskEnvelope, result: AgentResult, state: ProjectState) -> None:
        if result.task_id != task.task_id or result.input_hash != task.input_hash:
            raise ValueError("timeline agent result does not match task")
        if result.state_version_used != state.video.state_version:
            raise ValueError("STALE_RESULT: timeline agent used an obsolete state")
        if set(result.state_patch.set) - {"video.timeline_status"}:
            raise ValueError("timeline agent proposed forbidden state paths")
        if set(result.state_patch.invalidate) - {"render:final"}:
            raise ValueError("timeline agent proposed invalid invalidations")


def _merge_scoped(
    candidate: TimelineCandidate,
    current: TimelineArtifact | None,
    beat_ids: set[str],
) -> TimelineCandidate:
    """Keep everything outside the repair scope byte-identical."""

    assert current is not None
    if not beat_ids:
        return candidate
    new_clips = {item.beat_id: item for item in candidate.timeline.clips}
    clips = [
        new_clips.get(item.beat_id, item) if item.beat_id in beat_ids else item
        for item in current.timeline.clips
    ]
    new_captions = [item for item in candidate.captions if item.beat_id in beat_ids]
    captions = [
        item for item in current.captions if item.beat_id not in beat_ids
    ] + new_captions
    targeted_clip_ids = {item.clip_id for item in clips if item.beat_id in beat_ids}
    new_transforms = {item.clip_id: item for item in candidate.visual_transforms}
    transforms = [
        new_transforms.get(item.clip_id, item)
        if item.clip_id in targeted_clip_ids
        else item
        for item in current.visual_transforms
    ]
    overlays = [
        item for item in current.overlays if item.beat_id not in beat_ids
    ] + [item for item in candidate.overlays if item.beat_id in beat_ids]
    derivatives = [
        item for item in current.derivatives if item.beat_id not in beat_ids
    ] + [item for item in candidate.derivatives if item.beat_id in beat_ids]
    return candidate.model_copy(
        update={
            "derivatives": derivatives,
            "timeline": candidate.timeline.model_copy(update={"clips": clips}),
            "captions": sorted(captions, key=lambda item: (item.start_ms, item.cue_id)),
            "visual_transforms": transforms,
            "overlays": sorted(
                overlays, key=lambda item: (item.start_ms, item.overlay_id)
            ),
        }
    )


def _timeline_completion(
    task: TaskEnvelope,
    kwargs: dict[str, Any],
) -> CompletionSpec:
    voice = kwargs["voice"]
    assert isinstance(voice, VoiceArtifact)
    return CompletionSpec(
        artifact_refs=[ArtifactRef(kind="timeline_candidate", id=voice.project_id)],
        state_patch=StatePatch(
            set={"video.timeline_status": "ready"},
            invalidate=["render:final"],
        ),
        evaluation_target=(
            f"timeline:{voice.project_id}@{task.based_on_state_version + 1}"
        ),
    )
