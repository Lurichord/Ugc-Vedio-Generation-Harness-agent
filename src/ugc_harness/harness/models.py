from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .description import VideoDescription


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


DependencyStatus = Literal["current", "stale"]


class DependencySnapshot(HarnessModel):
    ref: str
    version: int = Field(ge=1)
    content_hash: str


class DependencyNode(HarnessModel):
    ref: str
    kind: str
    version: int = Field(ge=1)
    content_hash: str
    depends_on: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
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


class ProjectState(HarnessModel):
    # extra="ignore" tolerates fields written by older schema versions
    # (world_state / video_profile lived here before the description
    # document became the single source of truth).
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "project-state.v2"
    runtime_context: RuntimeContext
    video: VideoState
    description: VideoDescription | None = None
    execution: ExecutionState = Field(default_factory=ExecutionState)
    dependency_graph: DependencyGraphState = Field(
        default_factory=DependencyGraphState
    )
    trajectory: TrajectoryState = Field(default_factory=TrajectoryState)
