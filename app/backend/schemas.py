from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


StageName = Literal[
    "narrative", "voice", "editorial", "asset", "timeline", "render"
]
ReviewStatus = Literal["pending", "approved", "rejected"]


class IntakeNotice(BaseModel):
    notice_id: str
    role: Literal["user", "studio"]
    content: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PendingGate(BaseModel):
    kind: Literal["review", "running", "failed", "done"]
    stage: StageName | None = None
    next_stage: StageName | None = None
    question: str
    job_id: str | None = None
    error: str | None = None


class ProductionItem(BaseModel):
    ref: str
    kind: str
    beat_id: str
    title: str
    summary: str | None = None
    media_url: str | None = None


class StageSnapshot(BaseModel):
    stage: StageName
    label: str
    status: str
    required: bool = True
    user_approved: bool = False
    item_count: int = 0
    items: list[ProductionItem] = Field(default_factory=list)


class ProductionSnapshot(BaseModel):
    project_key: str | None = None
    project_id: str | None = None
    current_stage: StageName | None = None
    stages: list[StageSnapshot] = Field(default_factory=list)


class IntakeWorkspaceState(BaseModel):
    session_id: str
    project_key: str | None = None
    pending_gate: PendingGate | None = None
    notices: list[IntakeNotice] = Field(default_factory=list)


class CreateIntakeSessionResponse(BaseModel):
    session_id: str
    status: str
    reply: str
    address: str | None = None
    check_ok: bool | None = None
    issues: list[str] = Field(default_factory=list)
    project_id: str | None = None
    project_key: str | None = None
    job_id: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    intent: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    production: ProductionSnapshot = Field(default_factory=ProductionSnapshot)
    gate: PendingGate | None = None
    notices: list[IntakeNotice] = Field(default_factory=list)


class IntakeMessageRequest(BaseModel):
    message: str = Field(min_length=1)


class IntakeMessageResponse(BaseModel):
    session_id: str
    status: str
    reply: str
    address: str
    check_ok: bool
    issues: list[str] = Field(default_factory=list)
    project_id: str | None = None
    project_key: str | None = None
    job_id: str | None = None
    harness_action: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    intent: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    production: ProductionSnapshot = Field(default_factory=ProductionSnapshot)
    gate: PendingGate | None = None
    notices: list[IntakeNotice] = Field(default_factory=list)


class AttachProjectRequest(BaseModel):
    project_key: str = Field(min_length=1)


class CreateProjectRequest(BaseModel):
    topic: str = Field(min_length=1)
    project_name: str | None = None
    duration_seconds: int = Field(default=90, ge=60, le=120)
    platform: str = "douyin"
    audience: str = "对这个主题感兴趣的普通用户"
    goal: str | None = None
    tone: list[str] = Field(default_factory=list)
    creator_persona: str = "像朋友一样解释复杂话题的知识型创作者"
    video_profile: Literal["auto", "a_roll", "b_roll", "ab_roll"] = "auto"


class RunStageRequest(BaseModel):
    model: str | None = None
    voice_id: str | None = None
    image_model: str | None = None
    video_model: str | None = None


class ArtifactView(BaseModel):
    ref: str
    kind: str
    stage: StageName
    beat_id: str
    title: str
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    media_url: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    version: int | None = None
    node_status: str | None = None
    review_status: ReviewStatus = "pending"


class BeatView(BaseModel):
    beat_id: str
    order: int
    section_id: str | None = None
    proposition: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    artifacts: list[ArtifactView] = Field(default_factory=list)


class StageView(BaseModel):
    stage: StageName
    core_status: str
    critic_passed: bool
    user_approved: bool
    can_run: bool
    can_approve: bool
    can_advance: bool
    state_version: int
    graph_version: int
    issues: list[dict[str, Any]] = Field(default_factory=list)
    beats: list[BeatView] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    expected_state_version: int = Field(ge=0)
    beat_ids: list[str] = Field(min_length=1)


class ApprovalRecord(BaseModel):
    approval_id: str
    stage: StageName
    approved_refs: dict[str, int]
    beat_ids: list[str]
    state_version: int
    approved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class FeedbackRequest(BaseModel):
    stage: StageName
    beat_id: str
    target_ref: str
    instruction: str = Field(min_length=2)
    expected_state_version: int = Field(ge=0)
    expected_node_version: int = Field(ge=1)


class FeedbackRecord(BaseModel):
    feedback_id: str
    stage: StageName
    beat_id: str
    target_ref: str
    node_version: int
    instruction: str
    status: Literal["open", "repairing", "resolved", "cancelled"] = "open"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved_at: str | None = None


class TaskEvent(BaseModel):
    event_id: str
    project_id: str
    task_id: str | None = None
    stage: StageName
    event_type: str
    beat_ids: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)
    message: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    duration_ms: int | None = None


class TimelineItemView(BaseModel):
    id: str
    ref: str
    track: str
    beat_id: str
    start_ms: int
    end_ms: int
    label: str
    media_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "current"


class UnifiedTimelineView(BaseModel):
    duration_ms: int
    tracks: dict[str, list[TimelineItemView]]


class ProjectSummary(BaseModel):
    project_id: str
    project_name: str
    topic: str | None = None
    path_key: str
    current_stage: StageName
    state_version: int
    updated_at: str | None = None

