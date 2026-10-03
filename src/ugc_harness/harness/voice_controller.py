from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..agents.generic import (
    CompletionSpec,
    EnvironmentToolModel,
    GenericAgent,
)
from ..agents.narrative_agent.models import NarrativeArtifact
from ..agents.voice_agent.capabilities import TTSProvider, VoiceCapabilities
from ..agents.voice_agent.models import VoiceArtifact
from ..agents.voice_agent.tools import (
    VOICE_TOOLS,
    make_voice_transport,
    voice_task_context,
)
from ..agents.voice_agent.voice_select import VoicePicker
from ..evaluators.voice_critic import VoiceCritic
from ..tools.registry import ToolRegistry
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
from .transitions import transition_after_review
from .dependencies import DependencyGraph
from .dependency_builders import (
    has_content_graph,
    narrative_commits,
    refs_with_prefix,
    voice_commits,
)
from .description_realization import apply_voice_realization
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
    def __init__(
        self,
        agent: GenericAgent,
        critic: VoiceCritic,
        voice_id: str | None = None,
    ) -> None:
        self.agent = agent
        self.critic = critic
        self.voice_id = voice_id

    @classmethod
    def from_provider(
        cls,
        provider: TTSProvider,
        voice_id: str | None = None,
        tool_model: object | None = None,
        voice_picker: VoicePicker | None = None,
    ) -> "VoiceHarnessController":
        capabilities = VoiceCapabilities(provider)
        tools = ToolRegistry()
        for tool_name in VOICE_TOOLS:
            tools.register(tool_name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> object:
            del task
            narrative = kwargs["narrative"]
            assert isinstance(narrative, NarrativeArtifact)
            return make_voice_transport(
                capabilities,
                narrative,
                kwargs["project_dir"],
                override_voice_id=voice_id,
                picker=voice_picker,
                instruction=kwargs.get("instruction"),
                current_voice=kwargs.get("current_voice"),
            )

        def context_builder(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> dict[str, Any]:
            del task
            narrative = kwargs["narrative"]
            assert isinstance(narrative, NarrativeArtifact)
            return voice_task_context(
                narrative,
                override_voice_id=voice_id,
                instruction=kwargs.get("instruction"),
                current_voice=kwargs.get("current_voice"),
            )

        return cls(
            GenericAgent(
                tool_model=tool_model or EnvironmentToolModel(),
                transport_factory=transport_factory,
                candidate_type=VoiceArtifact,
                completion_builder=_voice_completion,
                context_builder=context_builder,
                capability_tools=tools,
            ),
            VoiceCritic(),
            voice_id,
        )

    def create_task(
        self,
        narrative: NarrativeArtifact,
        state: ProjectState,
    ) -> TaskEnvelope:
        graph = DependencyGraph(state.dependency_graph)
        return TaskEnvelope(
            task_id=(
                f"task_voice_{narrative.brief.project_id}_v"
                f"{state.context.state_version}"
            ),
            agent="voice_agent",
            goal="生成完整配音、原生字级时间戳与 RealizedBeat",
            scope=TaskScope(project_id=narrative.brief.project_id),
            based_on_state_version=state.context.state_version,
            allowed_tools=list(VOICE_TOOLS),
            required_outputs=["voice_artifact"],
            forbidden_actions=[
                "modify_narrative",
                "modify_visuals",
                "modify_assets",
                "modify_timeline",
            ],
            acceptance_criteria=[
                "所有讲解/旁白段均有音频",
                "旁白 WAV 存在且非空",
                "有讲解的步骤或节拍均形成 RealizedBeat",
                "输出通过独立 Voice Critic",
            ],
            budget=TaskBudget(max_steps=8, max_retries=1),
            input_hash=self._input_hash(narrative),
            dependency_snapshot=graph.snapshot(_voice_snapshot_refs(state)),
        )

    def run(
        self,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        state: ProjectState,
        task: TaskEnvelope | None = None,
        instruction: str | None = None,
    ) -> VoiceHarnessRun:
        if state.context.project_id != narrative.brief.project_id:
            raise ValueError("project state and narrative have different project_id")
        if state.evaluation.stages.narrative != "passed":
            raise ValueError("voice_agent requires an approved narrative artifact")
        is_repair = bool(task and task.scope.target_refs)
        if state.evaluation.stages.voice not in {"ready", "needs_revision", "stale"} and not (
            is_repair and state.evaluation.stages.voice == "passed"
        ):
            raise ValueError("voice_agent is not ready in project state")
        graph = DependencyGraph(state.dependency_graph)
        if not has_content_graph(state.dependency_graph.nodes):
            graph.commit_batch(
                task_id=f"bootstrap_narrative_{narrative.brief.project_id}",
                produced_by="narrative_agent",
                commits=narrative_commits(narrative),
            )
        envelope = task or self.create_task(narrative, state)
        if envelope.based_on_state_version != state.context.state_version:
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
        current_voice = (
            _load_voice_artifact(project_dir) if envelope.scope.target_refs else None
        )
        execution = self.agent.run(
            envelope,
            narrative=narrative,
            project_dir=project_dir,
            instruction=instruction,
            current_voice=current_voice,
        )
        artifact = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            artifact, VoiceArtifact
        ):
            raise RuntimeError(execution.result.error or "voice agent did not complete")
        self._validate_result(envelope, execution.result, state)
        graph.validate_snapshot(envelope.dependency_snapshot)
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        evaluation = self.critic.evaluate(
            artifact,
            narrative,
            project_dir,
            target_ref,
            lock_world_voice=not envelope.scope.target_refs,
        )
        committed_version = state.context.state_version + 1
        transition = transition_after_review(
            current_agent="voice_agent",
            evaluation=evaluation,
            committed_state_version=committed_version,
            approved_target=(
                "repair_scheduler"
                if envelope.scope.target_refs
                else "timeline_agent"
                if narrative.planning.planning_type == "tutorial"
                else None
            ),
        )
        next_state = state.model_copy(deep=True)
        next_state.context.state_version = committed_version
        next_state.evaluation.stages.voice = (
            "passed" if evaluation.passed else "needs_revision"
        )
        is_tutorial = narrative.planning.planning_type == "tutorial"
        if is_tutorial:
            next_state.evaluation.stages.editorial = "not_required"
            if evaluation.passed:
                timeline_status = str(state.evaluation.stages.timeline)
                if timeline_status in {
                    "passed",
                    "needs_revision",
                    "stale",
                    "locked",
                }:
                    next_state.evaluation.stages.render = "ready"
                else:
                    next_state.evaluation.stages.timeline = "ready"
                    next_state.evaluation.stages.render = "pending"
            elif str(state.evaluation.stages.timeline) not in {
                "passed",
                "needs_revision",
                "stale",
                "locked",
            }:
                next_state.evaluation.stages.timeline = "blocked"
        else:
            next_state.evaluation.stages.editorial = (
                "ready" if evaluation.passed else "blocked"
            )
        if evaluation.passed:
            apply_voice_realization(next_state, artifact)
        next_graph = DependencyGraph(next_state.dependency_graph)
        commits = voice_commits(artifact, next_state.production.voice)
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
        next_state.context.available_tools = sorted(
            set(next_state.context.available_tools) | self.agent.tools.names
        )
        self.voice_id = artifact.voice_plan.speaker.voice_id
        next_state.context.available_models["tts"] = [self.voice_id]
        return VoiceHarnessRun(
            artifact=artifact,
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
        if result.state_version_used != state.context.state_version:
            raise ValueError("STALE_RESULT: voice agent used an obsolete state version")
        if set(result.state_patch.set) - {"video.voice_status"}:
            raise ValueError("voice agent proposed forbidden state paths")
        allowed = {"editorial:all", "timeline:all", "render:final"}
        if set(result.state_patch.invalidate) - allowed:
            raise ValueError("voice agent proposed invalid invalidations")


def _load_voice_artifact(project_dir: str | Path) -> VoiceArtifact | None:
    path = Path(project_dir) / "voice_artifact.json"
    if not path.is_file():
        return None
    return VoiceArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def _voice_snapshot_refs(state: ProjectState) -> list[str]:
    nodes = state.dependency_graph.nodes
    utterances = refs_with_prefix(nodes, "utterance:")
    if utterances:
        return utterances
    return refs_with_prefix(nodes, "beat:", "scene:", "step:")


def _voice_completion(
    task: TaskEnvelope,
    kwargs: dict[str, Any],
) -> CompletionSpec:
    narrative = kwargs["narrative"]
    assert isinstance(narrative, NarrativeArtifact)
    is_tutorial = narrative.planning.planning_type == "tutorial"
    return CompletionSpec(
        artifact_refs=[
            ArtifactRef(kind="voice", id=narrative.brief.project_id)
        ],
        state_patch=StatePatch(
            set={"video.voice_status": "ready"},
            invalidate=(
                ["timeline:all", "render:final"]
                if is_tutorial
                else ["editorial:all", "timeline:all", "render:final"]
            ),
        ),
        evaluation_target=(
            f"voice:{narrative.brief.project_id}@{task.based_on_state_version + 1}"
        ),
    )
