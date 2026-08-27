"""Content-plane models: what the video *is*, independent of execution.

Layering contract (imports must only point downward):

    shared.models (StrictModel)
        <- profiles.models
        <- content (this module: world / structure / script / shot content)
        <- harness.description (VideoDescription document built from content)
        <- harness.models (ProjectState ledger, embeds VideoDescription)
        <- agents.* (artifact contracts re-export content models)

Models here describe the video itself and are shared by the description
document, the narrative artifacts, and the critics. They must not import
from ``harness`` or ``agents``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .shared.models import StrictModel


SectionRole = Literal["hook", "body", "close"]
ProductionMode = Literal["auto", "explainer", "drama", "tutorial"]
NarrativeFormatId = Literal["explainer", "drama", "tutorial"]
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


# ---------------------------------------------------------------------------
# Brief target specs (components of CreativeBrief)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# World layer
# ---------------------------------------------------------------------------


class WorldEntity(StrictModel):
    entity_id: str
    name: str
    kind: Literal[
        "person", "organization", "place", "product", "concept", "event", "other"
    ]
    narrative_role: str
    description: str


class WorldClaim(StrictModel):
    claim_id: str
    statement: str
    epistemic_status: Literal[
        "given_by_brief", "to_verify", "interpretation", "hypothesis"
    ]
    evidence_required: bool

    @model_validator(mode="after")
    def require_evidence_for_unverified_fact(self) -> "WorldClaim":
        if self.epistemic_status == "to_verify" and not self.evidence_required:
            raise ValueError("to_verify claims must require evidence")
        return self


class CausalLink(StrictModel):
    cause: str
    effect: str
    explanation: str


class ArollVoiceProfile(StrictModel):
    gender: Literal["male", "female", "neutral"]
    age_style: Literal["young", "mature", "senior"]
    tone: str
    pace: Literal["slow", "natural", "fast"] = "natural"


class ArollCharacter(StrictModel):
    character_id: str
    visual_description: str
    voice_profile: ArollVoiceProfile


class VideoWorldState(StrictModel):
    """The content world that must remain coherent throughout this video."""

    topic_frame: str
    entities: list[WorldEntity] = Field(min_length=1)
    claims: list[WorldClaim] = Field(min_length=1)
    causal_links: list[CausalLink] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    narrative_boundaries: list[str] = Field(default_factory=list)
    aroll_character: ArollCharacter | None = None

    @model_validator(mode="after")
    def require_unique_ids(self) -> "VideoWorldState":
        entity_ids = [item.entity_id for item in self.entities]
        claim_ids = [item.claim_id for item in self.claims]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("world state entity_id values must be unique")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("world state claim_id values must be unique")
        return self


# ---------------------------------------------------------------------------
# Structure layer: explainer
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Structure layer: drama
# ---------------------------------------------------------------------------


class DramaCharacter(StrictModel):
    character_id: str
    name: str
    description: str
    dramatic_objective: str | None = None
    appearance_constraints: list[str] = Field(default_factory=list)
    voice_constraints: list[str] = Field(default_factory=list)


class DramaScene(StrictModel):
    scene_id: str
    order: int = Field(default=1, ge=1)
    location_id: str
    purpose: str
    character_ids: list[str] = Field(default_factory=list)
    emotional_turn: str | None = None
    continuity_constraints: list[str] = Field(default_factory=list)


class DramaAction(StrictModel):
    action_id: str
    order: int = Field(ge=1)
    scene_id: str
    description: str
    character_ids: list[str] = Field(default_factory=list)
    objective: str | None = None
    reaction: str | None = None
    dialogue_lines: list[str] = Field(default_factory=list)
    state_changes: list[str] = Field(default_factory=list)
    camera_instruction: str = "中景，保持人物与动作清晰可见"
    ambient_audio: str | None = None
    target_duration_ms: int = Field(default=5_000, ge=1_000, le=15_000)


# ---------------------------------------------------------------------------
# Structure layer: tutorial
# ---------------------------------------------------------------------------


class TutorialMaterial(StrictModel):
    material_id: str
    name: str
    quantity: str | None = None


class TutorialStep(StrictModel):
    step_id: str
    order: int = Field(ge=1)
    instruction: str
    input_state: str | None = None
    expected_result: str | None = None
    visual_evidence: list[str] = Field(default_factory=list)
    common_mistakes: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)


class TutorialAction(StrictModel):
    action_id: str
    step_id: str
    description: str
    subject_ref: str | None = None
    critical_detail: str | None = None
    target_duration_ms: int = Field(default=8000, ge=1000, le=30000)


# ---------------------------------------------------------------------------
# Script / spoken-word content
# ---------------------------------------------------------------------------


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


class TutorialExplanationSegment(StrictModel):
    explanation_segment_id: str
    step_id: str
    placement: Literal["before_action", "during_action", "after_action"]
    text: str = Field(min_length=2)


# ---------------------------------------------------------------------------
# Shot layer: the universal production currency
# ---------------------------------------------------------------------------


class ExplainerVisualSpec(StrictModel):
    realization_type: Literal["explainer"] = "explainer"
    visual_source: str


class DramaVisualSpec(StrictModel):
    realization_type: Literal["generated_scene"] = "generated_scene"
    scene_id: str
    character_ids: list[str] = Field(default_factory=list)
    location_id: str
    action_description: str
    camera_instruction: str
    continuity_constraints: list[str] = Field(default_factory=list)
    generation_prompt: str


class TutorialVisualSpec(StrictModel):
    realization_type: Literal["procedure_demo"] = "procedure_demo"
    step_id: str
    action_id: str
    subject_ref: str
    hands_required: bool = True
    camera_angle: Literal["top_down", "close_up", "medium", "macro"]
    critical_detail: str


VisualRealizationSpec = Annotated[
    ExplainerVisualSpec | DramaVisualSpec | TutorialVisualSpec,
    Field(discriminator="realization_type"),
]


class ExternalNarrationAudioSpec(StrictModel):
    audio_mode: Literal["external_narration"] = "external_narration"
    script_segment_ids: list[str] = Field(min_length=1)
    speaker_id: str | None = None


class EmbeddedSceneAudioSpec(StrictModel):
    audio_mode: Literal["embedded_in_video"] = "embedded_in_video"
    dialogue_lines: list[str] = Field(default_factory=list)
    ambient_audio: str | None = None
    voice_agent_required: Literal[False] = False


class MixedAudioSpec(StrictModel):
    audio_mode: Literal["mixed"] = "mixed"
    narration_segment_ids: list[str] = Field(default_factory=list)
    preserve_source_audio: bool = True
    source_audio_types: list[str] = Field(default_factory=list)


AudioRealizationSpec = Annotated[
    ExternalNarrationAudioSpec | EmbeddedSceneAudioSpec | MixedAudioSpec,
    Field(discriminator="audio_mode"),
]


class TimingSpec(StrictModel):
    duration_driver: Literal[
        "narration",
        "generated_clip",
        "demonstration_action",
        "fixed",
    ]
    target_duration_ms: int | None = Field(default=None, gt=0)


class ExplainerShotPayload(StrictModel):
    payload_type: Literal["explainer"] = "explainer"
    planned_beat_id: str
    script_segment_ids: list[str] = Field(min_length=1)
    narration_text: str = Field(min_length=1)
    visual_intent: str


class DramaShotPayload(StrictModel):
    payload_type: Literal["drama"] = "drama"
    scene_id: str
    action_ids: list[str] = Field(min_length=1)
    dialogue_lines: list[str] = Field(default_factory=list)


class TutorialShotPayload(StrictModel):
    payload_type: Literal["tutorial"] = "tutorial"
    step_id: str
    action_ids: list[str] = Field(min_length=1)
    narration_text: str | None = None


ShotPayload = Annotated[
    ExplainerShotPayload | DramaShotPayload | TutorialShotPayload,
    Field(discriminator="payload_type"),
]


class ProductionShot(StrictModel):
    """One continuously realizable audiovisual unit for the timeline."""

    shot_id: str
    order: int = Field(ge=1)
    shot_kind: Literal["explainer", "drama", "tutorial"]
    purpose: str
    source_refs: list[str] = Field(min_length=1)
    world_state_before_ref: str | None = None
    world_state_after_ref: str | None = None
    visual: VisualRealizationSpec
    audio: AudioRealizationSpec
    timing: TimingSpec
    payload: ShotPayload

    @model_validator(mode="after")
    def validate_format_alignment(self) -> "ProductionShot":
        expected = {
            "explainer": ("explainer", "external_narration", "explainer"),
            "drama": ("generated_scene", "embedded_in_video", "drama"),
            "tutorial": ("procedure_demo", None, "tutorial"),
        }[self.shot_kind]
        if self.visual.realization_type != expected[0]:
            raise ValueError("shot_kind does not match visual realization_type")
        if expected[1] is not None and self.audio.audio_mode != expected[1]:
            raise ValueError("shot_kind does not match audio_mode")
        if self.payload.payload_type != expected[2]:
            raise ValueError("shot_kind does not match payload_type")
        return self
