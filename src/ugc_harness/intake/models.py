"""Persistent memory and one agent decision. Not a turn-routing pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import ConfigDict, Field

from ..content import (
    AudienceSpec,
    CommunicationSpec,
    ContentPolicy,
    NarrativeFormatId,
    ProductionMode,
    TargetSpec,
)
from ..harness.models import ArtifactStatus
from ..profiles.models import VideoProfileDecision, VideoProfileRequest
from ..shared.models import StrictModel


IntakeStatus = Literal["waiting_user", "done"]
MessageRole = Literal["user", "agent"]
KnowledgeLevel = Literal["beginner", "general", "expert"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BriefDraft(StrictModel):
    """Working copy of CreativeBrief. Incomplete fields stay None."""

    project_id: str | None = None
    project_name: str | None = None
    topic: str | None = None
    production_mode: ProductionMode | None = None
    video_profile: VideoProfileRequest | None = None
    target: TargetSpec | None = None
    audience: AudienceSpec | None = None
    communication: CommunicationSpec | None = None
    content_policy: ContentPolicy | None = None


class TargetPatch(StrictModel):
    platform: str | None = None
    duration_target_ms: int | None = None
    aspect_ratio: str | None = None
    language: str | None = None


class AudiencePatch(StrictModel):
    description: str | None = None
    knowledge_level: KnowledgeLevel | None = None


class CommunicationPatch(StrictModel):
    goal: str | None = None
    tone: list[str] | None = None
    creator_persona: str | None = None


class BriefPatch(StrictModel):
    """Partial brief write. Null means leave the current memory value alone."""

    project_id: str | None = None
    project_name: str | None = None
    topic: str | None = None
    production_mode: ProductionMode | None = None
    video_profile: VideoProfileRequest | None = None
    target: TargetPatch | None = None
    audience: AudiencePatch | None = None
    communication: CommunicationPatch | None = None
    content_policy: ContentPolicy | None = None


class IntentDraft(StrictModel):
    """Working copy of VideoIntent. Presentation stays empty until narrative commits it."""

    format_id: NarrativeFormatId | None = None
    topic: str | None = None
    one_sentence_thesis: str | None = None
    promise: str | None = None
    audience: AudienceSpec | None = None
    communication: CommunicationSpec | None = None
    target: TargetSpec | None = None
    content_policy: ContentPolicy | None = None
    presentation: VideoProfileDecision | None = None


class ProgressSnapshot(StrictModel):
    """VideoState statuses only. No shots, graph, or artifacts."""

    project_id: str | None = None
    state_version: int = 0
    narrative: ArtifactStatus = "pending"
    script: ArtifactStatus = "pending"
    voice: ArtifactStatus = "pending"
    editorial: ArtifactStatus = "pending"
    asset: ArtifactStatus = "pending"
    timeline: ArtifactStatus = "pending"
    render: ArtifactStatus = "pending"


class Inbound(StrictModel):
    text: str


class IntakeMessage(StrictModel):
    role: MessageRole
    content: str
    created_at: str = Field(default_factory=utc_now)


class IntakeSession(StrictModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = "intake-session.v2"
    session_id: str
    project_id: str | None = None
    project_dir: str | None = None
    created_at: str
    updated_at: str
    status: IntakeStatus = "waiting_user"
    working_brief: BriefDraft = Field(default_factory=BriefDraft)
    working_intent: IntentDraft = Field(default_factory=IntentDraft)
    progress: ProgressSnapshot = Field(default_factory=ProgressSnapshot)
    last_message: str | None = None
    messages: list[IntakeMessage] = Field(default_factory=list)


class AgentDecision(StrictModel):
    """One step: optional tool, or a user-facing decision."""

    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    done: bool = False


class AgentYield(StrictModel):
    session: IntakeSession
    message: str
    done: bool = False


class HostResult(StrictModel):
    session: IntakeSession
    message: str
    done: bool = False
