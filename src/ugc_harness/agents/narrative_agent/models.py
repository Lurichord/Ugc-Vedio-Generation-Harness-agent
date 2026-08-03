from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ...harness.models import VideoWorldState
from ...profiles.models import VideoProfileDecision, VideoProfileRequest
from ...shared.models import StrictModel


SectionRole = Literal["hook", "body", "close"]
BeatRole = Literal[
    "question",
    "expected_answer",
    "reveal",
    "claim",
    "evidence",
    "explanation",
    "example",
    "contrast",
    "deepening",
    "implication",
    "payoff",
    "callback",
]
Relation = Literal[
    "opening",
    "continuation",
    "cause",
    "contrast",
    "escalation",
    "evidence_for",
    "example_of",
    "resolution",
    "callback",
]


class TargetSpec(StrictModel):
    platform: str = "douyin"
    duration_target_ms: int = Field(default=90_000, ge=60_000, le=120_000)
    aspect_ratio: str = "9:16"
    language: str = "zh-CN"


class AudienceSpec(StrictModel):
    description: str = "对主题感兴趣、但没有专业背景的普通用户"
    knowledge_level: Literal["beginner", "general", "expert"] = "general"


class CommunicationSpec(StrictModel):
    goal: str
    tone: list[str] = Field(
        default_factory=lambda: ["conversational", "clear", "slightly_surprising"]
    )
    creator_persona: str = "像朋友一样解释复杂话题的知识型创作者"


class ContentPolicy(StrictModel):
    factual_claims_require_sources: bool = True
    generated_media_cannot_be_evidence: bool = True
    avoid_fabricated_personal_experience: bool = True


class CreativeBrief(StrictModel):
    project_id: str
    project_name: str | None = None
    topic: str = Field(min_length=2)
    target: TargetSpec
    audience: AudienceSpec
    communication: CommunicationSpec
    video_profile: VideoProfileRequest = "auto"
    content_policy: ContentPolicy = Field(default_factory=ContentPolicy)


class Section(StrictModel):
    section_id: str
    role: SectionRole
    target_duration_ms: int = Field(gt=0)
    goal: str
    attention_strategy: str


class AudienceDelta(StrictModel):
    knowledge_added: list[str] = Field(default_factory=list)
    belief_update: str | None = None
    question_added: str | None = None
    question_resolved: str | None = None
    emotion_target: str


class EvidenceNeed(StrictModel):
    required: bool
    claim_to_verify: str | None = None
    acceptable_source_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_claim_when_evidence_is_required(self) -> "EvidenceNeed":
        if self.required and not self.claim_to_verify:
            raise ValueError("claim_to_verify is required when evidence is required")
        return self


class PlannedBeat(StrictModel):
    planned_beat_id: str
    section_id: str
    order: int = Field(ge=1)
    semantic_goal: str
    discourse_role: BeatRole
    relation_to_previous: Relation
    target_effect: str
    target_duration_ms: int = Field(ge=1_500, le=15_000)
    audience_delta: AudienceDelta
    evidence_need: EvidenceNeed
    visual_intent_hint: str


class PlanningArtifact(StrictModel):
    narrative_pattern: str
    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    sections: list[Section] = Field(min_length=3, max_length=3)
    beats: list[PlannedBeat] = Field(min_length=6, max_length=24)

    @model_validator(mode="after")
    def validate_graph_references(self) -> "PlanningArtifact":
        section_ids = {section.section_id for section in self.sections}
        unknown = {
            beat.section_id for beat in self.beats if beat.section_id not in section_ids
        }
        if unknown:
            raise ValueError(f"beats reference unknown sections: {sorted(unknown)}")
        ids = [beat.planned_beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("planned_beat_id values must be unique")
        if [beat.order for beat in self.beats] != list(range(1, len(self.beats) + 1)):
            raise ValueError("beat order must be contiguous and start at 1")
        character = self.world_state.aroll_character
        if self.video_profile.resolved in {"a_roll", "ab_roll"}:
            if character is None:
                raise ValueError("speaker-led planning requires world_state.aroll_character")
            if character.character_id != self.video_profile.character_id:
                raise ValueError("world-state character_id must match video_profile")
            if character.visual_description != self.video_profile.character_description:
                raise ValueError(
                    "world-state character description must match video_profile"
                )
        elif character is not None:
            raise ValueError("b_roll planning cannot define an A-roll character")
        return self


class DeliveryHint(StrictModel):
    speech_act: BeatRole
    emphasis_words: list[str] = Field(default_factory=list)
    pause_before_ms: int = Field(default=0, ge=0, le=2_000)
    pause_after_ms: int = Field(default=120, ge=0, le=2_000)
    energy: Literal["low", "medium", "high"] = "medium"


class ScriptSegment(StrictModel):
    script_segment_id: str
    planned_beat_id: str
    text: str = Field(min_length=2)
    delivery_hint: DeliveryHint


class ScriptArtifact(StrictModel):
    script_version: str = "v1"
    title_options: list[str] = Field(min_length=3, max_length=5)
    segments: list[ScriptSegment] = Field(min_length=6)


class QualityIssue(StrictModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    ref: str | None = None


class QualityReport(StrictModel):
    passed: bool
    planned_duration_ms: int
    estimated_script_duration_ms: int
    script_char_count: int
    beat_coverage: float
    evidence_claim_count: int
    issues: list[QualityIssue] = Field(default_factory=list)


class NarrativeArtifact(StrictModel):
    schema_version: str = "narrative.v2"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model: str
    brief: CreativeBrief
    planning: PlanningArtifact
    script: ScriptArtifact
    quality: QualityReport
