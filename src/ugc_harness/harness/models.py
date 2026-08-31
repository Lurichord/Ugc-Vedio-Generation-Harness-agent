from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .description import (
    CreativeBriefRecord,
    Deliverable,
    TimelineDescription,
    VideoDescription,
    VideoIntent,
    VideoStructure,
    VideoWorld,
    VoiceDesign,
    ShotDescription,
)


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


DependencyStatus = Literal["current", "stale"]


class DependencySnapshot(HarnessModel):
    ref: str
    version: int = Field(ge=1)
    content_hash: str = ""


EdgeFamily = Literal["plot", "identity", "element"]


class DependencyEdge(HarnessModel):
    to: str
    family: EdgeFamily

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_voice_family(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("family") == "voice":
            return {**value, "family": "element"}
        return value


class DependencyNode(HarnessModel):
    ref: str
    kind: str
    version: int = Field(ge=1)
    content_hash: str
    depends_on: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    dependency_versions: dict[str, int] = Field(default_factory=dict)
    dependency_hashes: dict[str, str] = Field(default_factory=dict)
    status: DependencyStatus = "current"
    produced_by: str | None = None
    last_task_id: str | None = None
    locked: bool = False


class DependencyGraphState(HarnessModel):
    graph_version: int = Field(default=0, ge=0)
    nodes: dict[str, DependencyNode] = Field(default_factory=dict)


class TaskScope(HarnessModel):
    project_id: str
    shot_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    beat_ids: list[str] = Field(default_factory=list)
    script_segment_ids: list[str] = Field(default_factory=list)
    visual_request_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)


class TaskBudget(HarnessModel):
    max_steps: int = Field(default=4, ge=2, le=32)
    max_retries: int = Field(default=1, ge=0, le=8)
    max_cost_usd: float | None = Field(default=None, ge=0)
    deadline_seconds: int = Field(default=300, ge=1)
    fallback_policy: Literal["fail", "use_best_available"] = "fail"


class TaskEnvelope(HarnessModel):
    task_id: str
    agent: str
    goal: str
    scope: TaskScope
    based_on_state_version: int = Field(ge=0)
    format_id: str | None = None
    agent_instructions: str | None = None
    allowed_tools: list[str] = Field(min_length=1)
    required_outputs: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    input_hash: str
    dependency_snapshot: list[DependencySnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_tools(self) -> "TaskEnvelope":
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed_tools must not contain duplicates")
        if len(self.required_outputs) != len(set(self.required_outputs)):
            raise ValueError("required_outputs must not contain duplicates")
        return self


class ArtifactRef(HarnessModel):
    kind: str
    id: str
    version: int = Field(default=1, ge=1)


class ActionRecord(HarnessModel):
    action_id: str
    agent: str
    task_id: str
    tool: str
    result: Literal["success", "rejected", "failed"]
    reason: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class StatePatch(HarnessModel):
    set: dict[str, Any] = Field(default_factory=dict)
    invalidate: list[str] = Field(default_factory=list)


class AgentResult(HarnessModel):
    task_id: str
    status: Literal[
        "completed", "failed", "blocked", "needs_user_input", "budget_exhausted"
    ]
    state_version_used: int = Field(ge=0)
    input_hash: str
    actions: list[ActionRecord] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    state_patch: StatePatch = Field(default_factory=StatePatch)
    evaluation_target: str | None = None
    error: str | None = None


class CriticIssue(HarnessModel):
    issue_id: str
    critic_id: str
    scope: str
    target_ref: str
    severity: Literal["error", "warning"]
    code: str
    diagnosis: str
    repair_options: list[str] = Field(default_factory=list)


class EvaluationResult(HarnessModel):
    critic_id: str
    target_ref: str
    passed: bool
    issues: list[CriticIssue] = Field(default_factory=list)


ArtifactStatus = Literal[
    "pending",
    "ready",
    "running",
    "passed",
    "failed",
    "stale",
    "blocked",
    "locked",
    "needs_user_input",
    "needs_revision",
    "not_required",
]


class RuntimeContext(HarnessModel):
    """Models, tools, and execution constraints available to the harness."""

    available_models: dict[str, list[str]] = Field(default_factory=dict)
    available_tools: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class VideoState(HarnessModel):
    project_id: str
    state_version: int = Field(ge=0)
    narrative_status: ArtifactStatus = "pending"
    script_status: ArtifactStatus = "pending"
    voice_status: ArtifactStatus = "pending"
    editorial_status: ArtifactStatus = "pending"
    asset_status: ArtifactStatus = "pending"
    timeline_status: ArtifactStatus = "pending"
    render_status: ArtifactStatus = "pending"


class TransitionRecord(HarnessModel):
    transition_id: str
    from_agent: str
    to_agent: str
    outcome: Literal["advance", "revise"]
    trigger_ref: str
    reason: str
    committed_state_version: int = Field(ge=1)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class GraphUpdateRecord(HarnessModel):
    update_id: str
    task_id: str
    graph_version_before: int = Field(ge=0)
    graph_version_after: int = Field(ge=1)
    committed: bool
    changed_refs: list[str] = Field(default_factory=list)
    refreshed_refs: list[str] = Field(default_factory=list)
    invalidated_refs: list[str] = Field(default_factory=list)
    rejected_refs: list[str] = Field(default_factory=list)
    reason: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


TaskKind = Literal["generation", "revision", "repair"]


class TaskTrajectoryRecord(HarnessModel):
    task_kind: TaskKind
    task: TaskEnvelope
    agent_result: AgentResult
    evaluation: EvaluationResult
    transition: TransitionRecord
    graph_update: GraphUpdateRecord
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PhaseTrajectory(HarnessModel):
    phase: str
    tasks: list[TaskTrajectoryRecord] = Field(default_factory=list)


class TrajectoryState(HarnessModel):
    phases: dict[str, PhaseTrajectory] = Field(default_factory=dict)


class ElementStatus(HarnessModel):
    """Execution tag for one description element, keyed by its ref."""

    status: ArtifactStatus = "pending"
    version: int = Field(default=1, ge=1)
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    generation_job_id: str | None = None
    cost_usd: float | None = Field(default=None, ge=0)


class ExecutionState(HarnessModel):
    """Execution plane: status tags hung on description elements by ref."""

    elements: dict[str, ElementStatus] = Field(default_factory=dict)


class ProjectContext(HarnessModel):
    """Who this project is and what the harness can use."""

    project_id: str = ""
    state_version: int = Field(default=0, ge=0)
    available_models: dict[str, list[str]] = Field(default_factory=dict)
    available_tools: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class CreativeState(HarnessModel):
    """What we promised to make: brief plus resolved intent."""

    brief: CreativeBriefRecord | None = None
    intent: VideoIntent | None = None


class ProductionState(HarnessModel):
    """Addressable content of the video. Modification writes here."""

    world: VideoWorld = Field(default_factory=VideoWorld)
    structure: VideoStructure | None = None
    voice: VoiceDesign | None = None
    shots: list[ShotDescription] = Field(default_factory=list)
    timeline: TimelineDescription | None = None
    deliverable: Deliverable | None = None


class StageStatusBoard(HarnessModel):
    narrative: ArtifactStatus = "pending"
    script: ArtifactStatus = "pending"
    voice: ArtifactStatus = "pending"
    editorial: ArtifactStatus = "pending"
    asset: ArtifactStatus = "pending"
    timeline: ArtifactStatus = "pending"
    render: ArtifactStatus = "pending"


class EvaluationState(HarnessModel):
    """Status tags on the same refs as production, plus stage summaries."""

    elements: dict[str, ElementStatus] = Field(default_factory=dict)
    stages: StageStatusBoard = Field(default_factory=StageStatusBoard)


_STAGE_ATTRS = {
    "narrative_status": "narrative",
    "script_status": "script",
    "voice_status": "voice",
    "editorial_status": "editorial",
    "asset_status": "asset",
    "timeline_status": "timeline",
    "render_status": "render",
}


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"expected mapping, got {type(value)!r}")


def _coerce_description(value: Any) -> VideoDescription | None:
    if value is None:
        return None
    if isinstance(value, VideoDescription):
        return value
    payload = dict(value)
    payload.pop("visual_language", None)
    payload.pop("sound_design", None)
    world = payload.get("world")
    if isinstance(world, dict):
        world = dict(world)
        world.pop("state", None)
        payload["world"] = world
    return VideoDescription.model_validate(payload)


def _legacy_project_state_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    video = payload.pop("video", None)
    runtime = payload.pop("runtime_context", None)
    description = payload.pop("description", None)
    execution = payload.pop("execution", None)

    context_data = _as_mapping(payload.get("context"))
    if runtime is not None:
        runtime_data = _as_mapping(runtime)
        context_data.setdefault(
            "available_models", runtime_data.get("available_models", {})
        )
        context_data.setdefault(
            "available_tools", runtime_data.get("available_tools", [])
        )
        context_data.setdefault("constraints", runtime_data.get("constraints", {}))
    if video is not None:
        video_data = _as_mapping(video)
        context_data["project_id"] = video_data.get(
            "project_id", context_data.get("project_id", "")
        )
        context_data["state_version"] = video_data.get(
            "state_version", context_data.get("state_version", 0)
        )

    evaluation_data = _as_mapping(payload.get("evaluation"))
    stages_data = _as_mapping(evaluation_data.get("stages"))
    if video is not None:
        video_data = _as_mapping(video)
        for source, dest in _STAGE_ATTRS.items():
            if source in video_data:
                stages_data[dest] = video_data[source]

    creative_data = _as_mapping(payload.get("creative"))
    production_data = _as_mapping(payload.get("production"))
    if isinstance(production_data.get("world"), dict):
        production_data["world"] = dict(production_data["world"])
        production_data["world"].pop("state", None)
    production_data.pop("visual_language", None)
    production_data.pop("sound_design", None)
    document = _coerce_description(description)
    if document is not None:
        creative_data.setdefault("intent", document.intent)
        production_data.setdefault("world", document.world)
        production_data.setdefault("structure", document.structure)
        production_data.setdefault("voice", document.voice)
        production_data.setdefault("shots", document.shots)
        production_data.setdefault("timeline", document.timeline)
        production_data.setdefault("deliverable", document.deliverable)

    elements = evaluation_data.get("elements") or {}
    if execution is not None:
        elements = _as_mapping(execution).get("elements", elements)
    evaluation_data["elements"] = elements
    evaluation_data["stages"] = stages_data

    payload["schema_version"] = "project-state.v3"
    payload["context"] = context_data
    payload["creative"] = creative_data
    payload["production"] = production_data
    payload["evaluation"] = evaluation_data
    return payload


class ProjectState(HarnessModel):
    # extra="ignore" drops leftover v2 keys after migration.
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "project-state.v3"
    context: ProjectContext = Field(default_factory=ProjectContext)
    creative: CreativeState = Field(default_factory=CreativeState)
    production: ProductionState = Field(default_factory=ProductionState)
    evaluation: EvaluationState = Field(default_factory=EvaluationState)
    dependency_graph: DependencyGraphState = Field(
        default_factory=DependencyGraphState
    )
    trajectory: TrajectoryState = Field(default_factory=TrajectoryState)

    @model_validator(mode="before")
    @classmethod
    def _accept_v2_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if any(
            key in data
            for key in ("video", "runtime_context", "description", "execution")
        ):
            return _legacy_project_state_payload(data)
        production = data.get("production")
        if isinstance(production, dict):
            production = dict(production)
            production.pop("visual_language", None)
            production.pop("sound_design", None)
            world = production.get("world")
            if isinstance(world, dict):
                world = dict(world)
                world.pop("state", None)
                production["world"] = world
            data = dict(data)
            data["production"] = production
        return data

    def apply_description(self, document: VideoDescription | None) -> None:
        if document is None:
            self.creative.intent = None
            self.production = ProductionState()
            return
        self.creative.intent = document.intent
        self.production.world = document.world
        self.production.structure = document.structure
        self.production.voice = document.voice
        self.production.shots = document.shots
        self.production.timeline = document.timeline
        self.production.deliverable = document.deliverable
