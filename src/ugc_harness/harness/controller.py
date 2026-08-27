from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from ..agents.generic import (
    CompletionSpec,
    GenericAgent,
    McpToolTransport,
)
from ..evaluators.narrative_critic import NarrativeCritic
from ..agents.narrative_agent.models import (
    CreativeBrief,
    DramaPlanningArtifact,
    NarrativeArtifact,
    NarrativeCandidate,
    PlanningArtifact,
    ScriptArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)
from ..tools.registry import ToolRegistry
from ..tools.mcp import StdioMCPServerConfig, narrative_stdio_server_config
from .models import (
    AgentResult,
    ArtifactRef,
    EvaluationResult,
    ProjectState,
    StatePatch,
    TaskEnvelope,
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
from .description_builder import build_video_description, initial_execution_state
from .narrative_formats import (
    NarrativeFormatRegistry,
    default_narrative_format_registry,
)


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
        agent: GenericAgent,
        critic: NarrativeCritic,
        model_name: str,
        *,
        state_version: int = 0,
        format_registry: NarrativeFormatRegistry | None = None,
    ) -> None:
        self.agent = agent
        self.critic = critic
        self.model_name = model_name
        self.state_version = state_version
        self.format_registry = format_registry or default_narrative_format_registry()

    @classmethod
    def from_mcp(
        cls,
        tool_model: object,
        model_name: str,
        *,
        state_version: int = 0,
        server: StdioMCPServerConfig | None = None,
        generation_context: dict[str, object] | None = None,
        format_registry: NarrativeFormatRegistry | None = None,
    ) -> "NarrativeHarnessController":
        choose_tool = getattr(tool_model, "choose_tool", None)
        if not callable(choose_tool):
            raise TypeError("tool_model must provide choose_tool(messages, tools)")
        active_registry = format_registry or default_narrative_format_registry()
        tools = ToolRegistry()
        # The registry remains the Harness capability inventory. Execution goes
        # through the discovered stdio MCP tools, never these placeholders.
        for tool_name in active_registry.capability_tools:
            tools.register(tool_name, lambda: None)
        selected_server = server or narrative_stdio_server_config()
        settings = getattr(tool_model, "settings", None)
        if settings is not None:
            api_key = getattr(settings, "api_key", None)
            base_url = getattr(settings, "base_url", None)
            child_env: dict[str, str] = {}
            if isinstance(api_key, str):
                child_env["VOLCENGINE_ARK_API_KEY"] = api_key
            if isinstance(base_url, str):
                child_env["VOLCENGINE_ARK_BASE_URL"] = base_url
            if child_env:
                selected_server = selected_server.with_environment(child_env)
        context = dict(generation_context or {})

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, object],
        ) -> McpToolTransport:
            brief = kwargs["brief"]
            assert isinstance(brief, CreativeBrief)
            return McpToolTransport(
                selected_server,
                configure_tool="narrative.configure_task",
                configure_payload={
                    "brief": brief.model_dump(mode="json"),
                    "model": model_name,
                    "generation_context": context,
                },
            )

        def context_builder(
            task: TaskEnvelope,
            kwargs: dict[str, object],
        ) -> dict[str, object]:
            brief = kwargs["brief"]
            assert isinstance(brief, CreativeBrief)
            return {"creative_brief": brief.model_dump(mode="json")}

        return cls(
            GenericAgent(
                tool_model=tool_model,  # type: ignore[arg-type]
                transport_factory=transport_factory,
                candidate_type=NarrativeCandidate,
                completion_builder=_narrative_completion,
                context_builder=context_builder,
                capability_tools=tools,
            ),
            NarrativeCritic(),
            model_name,
            state_version=state_version,
            format_registry=active_registry,
        )

    def create_task(self, brief: CreativeBrief) -> TaskEnvelope:
        input_hash = self._input_hash(brief)
        pack = self.format_registry.resolve(brief.production_mode)
        return pack.create_task(
            brief,
            state_version=self.state_version,
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
        if envelope.format_id is None:
            raise ValueError("Narrative TaskEnvelope requires format_id")
        if (
            brief.production_mode != "auto"
            and brief.production_mode != envelope.format_id
        ):
            raise RuntimeError(
                "CreativeBrief production_mode does not match TaskEnvelope"
            )
        pack = self.format_registry.resolve(envelope.format_id)
        execution = self.agent.run(envelope, brief=brief)
        candidate = execution.candidate
        missing_required = [
            name
            for name in envelope.required_outputs
            if getattr(candidate, name, None) is None
        ]
        if (
            execution.result.status != "completed"
            or candidate is None
            or missing_required
        ):
            raise RuntimeError(
                execution.result.error or "narrative agent did not complete"
            )
        if not isinstance(candidate, NarrativeCandidate):
            raise RuntimeError("narrative agent returned the wrong candidate type")
        if not isinstance(candidate.planning, pack.planning_schema):
            raise RuntimeError(
                f"{pack.format_id!r} candidate returned the wrong planning schema"
            )
        if isinstance(candidate.planning, PlanningArtifact) and not isinstance(
            candidate.script,
            ScriptArtifact,
        ):
            raise RuntimeError("explainer Narrative requires a ScriptArtifact")
        if isinstance(candidate.planning, DramaPlanningArtifact):
            if candidate.script is not None:
                raise RuntimeError("drama Narrative must use embedded scene dialogue")
            if candidate.shots is None:
                raise RuntimeError("drama Narrative requires ProductionShots")
        if isinstance(candidate.planning, TutorialPlanningArtifact):
            if not isinstance(candidate.script, TutorialScriptArtifact):
                raise RuntimeError("tutorial Narrative requires tutorial explanations")
            if candidate.shots is None:
                raise RuntimeError("tutorial Narrative requires ProductionShots")
        self._validate_result(envelope, execution.result)
        if state is not None:
            DependencyGraph(state.dependency_graph).validate_snapshot(
                envelope.dependency_snapshot
            )
        target_ref = execution.result.evaluation_target
        assert target_ref is not None
        quality, evaluation = self.critic.evaluate(
            brief,
            candidate.planning,
            candidate.script,
            target_ref,
            shots=candidate.shots,
        )
        artifact = NarrativeArtifact(
            model=self.model_name,
            brief=brief,
            planning=candidate.planning,
            script=candidate.script,
            shots=candidate.shots,
            quality=quality,
        )
        # Commit is controller-owned. The agent only proposed a StatePatch.
        committed_version = self.state_version + 1
        transition = transition_after_review(
            current_agent="narrative_agent",
            evaluation=evaluation,
            committed_state_version=committed_version,
            approved_target=(
                "repair_scheduler"
                if envelope.scope.target_refs
                else "asset_agent"
                if pack.format_id in {"drama", "tutorial"}
                else None
            ),
        )
        narrative_status = "passed" if evaluation.passed else "needs_revision"
        successor_status = "ready" if evaluation.passed else "blocked"
        direct_to_assets = pack.format_id in {"drama", "tutorial"}
        script_status = (
            "not_required" if evaluation.passed else "blocked"
        ) if pack.format_id == "drama" else narrative_status
        voice_status = (
            "not_required" if evaluation.passed else "blocked"
        ) if direct_to_assets else successor_status
        editorial_status = (
            "not_required" if evaluation.passed else "blocked"
        ) if direct_to_assets else "pending"
        asset_status = (
            successor_status if direct_to_assets else "pending"
        )
        if state is None:
            project_state = ProjectState(
                runtime_context=RuntimeContext(
                    available_models={"llm": [self.model_name]},
                    available_tools=sorted(self.agent.tools.names),
                    constraints={
                        "aspect_ratio": brief.target.aspect_ratio,
                        "language": brief.target.language,
                        "production_mode": brief.production_mode,
                        "narrative_format": pack.format_id,
                    },
                ),
                video=VideoState(
                    project_id=brief.project_id,
                    state_version=committed_version,
                    narrative_status=narrative_status,
                    script_status=script_status,
                    voice_status=voice_status,
                    editorial_status=editorial_status,
                    asset_status=asset_status,
                    timeline_status="pending",
                    render_status="pending",
                ),
                trajectory=TrajectoryState(),
            )
        else:
            project_state = state.model_copy(deep=True)
            project_state.video.state_version = committed_version
            project_state.video.narrative_status = narrative_status
            project_state.video.script_status = script_status
            project_state.video.voice_status = voice_status
            project_state.video.editorial_status = (
                editorial_status if direct_to_assets else "blocked"
            )
            project_state.video.asset_status = (
                asset_status if direct_to_assets else "blocked"
            )
            project_state.video.timeline_status = "blocked"
            project_state.video.render_status = "blocked"
            if evaluation.passed:
                project_state.runtime_context.constraints.update(
                    {
                        "production_mode": brief.production_mode,
                        "narrative_format": pack.format_id,
                    }
                )
            project_state.runtime_context.available_tools = sorted(
                set(project_state.runtime_context.available_tools)
                | self.agent.tools.names
            )
            models = project_state.runtime_context.available_models.setdefault(
                "llm", []
            )
            if self.model_name not in models:
                models.append(self.model_name)
        if evaluation.passed:
            # The approved candidate becomes the authoritative description
            # document; execution tags are re-keyed to its element refs.
            project_state.description = build_video_description(artifact)
            project_state.execution = initial_execution_state(
                project_state.description
            )
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
            "asset:all",
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


def _narrative_completion(
    task: TaskEnvelope,
    kwargs: dict[str, object],
) -> CompletionSpec:
    """Stage semantics of a completed narrative run; harness-owned."""

    brief = kwargs["brief"]
    assert isinstance(brief, CreativeBrief)
    refs_by_output = {
        "world_state": ArtifactRef(
            kind="narrative_world_state",
            id=brief.project_id,
        ),
        "planning": ArtifactRef(kind="narrative_plan", id=brief.project_id),
        "script": ArtifactRef(kind="script", id=brief.project_id),
        "shots": ArtifactRef(kind="shot_plan", id=brief.project_id),
    }
    is_drama = task.format_id == "drama"
    skips_voice = task.format_id in {"drama", "tutorial"}
    return CompletionSpec(
        artifact_refs=[
            refs_by_output[output]
            for output in task.required_outputs
            if output in refs_by_output
        ],
        state_patch=StatePatch(
            set={
                "video.narrative_status": "ready",
                "video.script_status": (
                    "not_required" if is_drama else "ready"
                ),
            },
            invalidate=(
                ["asset:all", "timeline:all", "render:final"]
                if skips_voice
                else [
                    "voice:all",
                    "editorial:all",
                    "timeline:all",
                    "render:final",
                ]
            ),
        ),
        evaluation_target=(
            f"narrative:{brief.project_id}@{task.based_on_state_version + 1}"
        ),
    )
