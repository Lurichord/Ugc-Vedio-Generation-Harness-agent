"""The VideoDescription document: a complete answer to "what is this video?".

This is the content plane of ProjectState. Planning agents propose ``spec``
fields; production stages fill ``realization`` fields (media bindings, audio
files, the rendered deliverable). Execution bookkeeping (statuses, attempts,
budgets) lives outside this document in the execution plane.

Cross-layer references use one ref syntax shared with the dependency graph:

    section:sec_hook   beat:b3      scene:s2       action:a5
    step:s1            utterance:u4 shot:shot_3    clip:c2

Only decision-relevant content belongs here. Word-level timestamps and media
bytes stay on disk; the document stores paths plus content hashes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..content import (
    ArollVoiceProfile,
    AudienceSpec,
    CommunicationSpec,
    ContentPolicy,
    DeliveryHint,
    DramaAction,
    DramaScene,
    NarrativeFormatId,
    PlannedBeat,
    ProductionShot,
    Section,
    TargetSpec,
    TutorialAction,
    TutorialMaterial,
    TutorialStep,
    VideoWorldState,
)
from ..profiles.models import VideoProfileDecision
from ..shared.models import StrictModel


# ---------------------------------------------------------------------------
# Intent layer
# ---------------------------------------------------------------------------


class VideoIntent(StrictModel):
    """What video this is, for whom, and what it promises."""

    format_id: NarrativeFormatId
    topic: str
    one_sentence_thesis: str
    promise: str
    audience: AudienceSpec
    communication: CommunicationSpec
    target: TargetSpec
    content_policy: ContentPolicy = Field(default_factory=ContentPolicy)
    presentation: VideoProfileDecision


# ---------------------------------------------------------------------------
# World layer
# ---------------------------------------------------------------------------


class CastMember(StrictModel):
    """A person who appears or speaks; a cross-stage consistency asset."""

    character_id: str
    name: str
    role: Literal["narrator", "performer"]
    description: str
    dramatic_objective: str | None = None
    appearance_constraints: list[str] = Field(default_factory=list)
    voice_constraints: list[str] = Field(default_factory=list)
    voice_profile: ArollVoiceProfile | None = None


class VideoWorld(StrictModel):
    state: VideoWorldState
    cast: list[CastMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cast_entities(self) -> "VideoWorld":
        ids = [member.character_id for member in self.cast]
        if len(ids) != len(set(ids)):
            raise ValueError("cast character_id values must be unique")
        return self


# ---------------------------------------------------------------------------
# Visual language layer (global style anchor)
# ---------------------------------------------------------------------------


class VisualLanguage(StrictModel):
    """Whole-video look; shot generation prompts must reference this anchor."""

    style_keywords: list[str] = Field(min_length=1)
    color_mood: str
    lighting: str | None = None
    camera_style: str | None = None
    reference_notes: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sound design layer (placeholder until a BGM/mix capability exists)
# ---------------------------------------------------------------------------


class BgmSpec(StrictModel):
    style: str
    mood: str
    anchor_refs: list[str] = Field(default_factory=list)


class SfxCue(StrictModel):
    anchor_ref: str
    effect: str


class SoundDesign(StrictModel):
    bgm: BgmSpec | None = None
    sfx_cues: list[SfxCue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Structure layer: the format-specific middle
# ---------------------------------------------------------------------------


class ExplainerStructure(StrictModel):
    kind: Literal["explainer"] = "explainer"
    narrative_pattern: str
    sections: list[Section] = Field(min_length=3, max_length=3)
    beats: list[PlannedBeat] = Field(min_length=1)


class DramaStructure(StrictModel):
    kind: Literal["drama"] = "drama"
    premise: str
    scenes: list[DramaScene] = Field(min_length=1)
    actions: list[DramaAction] = Field(min_length=1)


class ResultDefinition(StrictModel):
    description: str
    success_criteria: list[str] = Field(default_factory=list)


class TutorialStructure(StrictModel):
    kind: Literal["tutorial"] = "tutorial"
    objective: str
    result: ResultDefinition
    materials: list[TutorialMaterial] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    steps: list[TutorialStep] = Field(min_length=1)
    actions: list[TutorialAction] = Field(min_length=1)
    coverage_requirements: list[str] = Field(default_factory=list)


VideoStructure = Annotated[
    ExplainerStructure | DramaStructure | TutorialStructure,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Voice layer: who says what, where, and how
# ---------------------------------------------------------------------------


class VoiceSpeakerSpec(StrictModel):
    provider: str
    voice_id: str
    language: str = "zh-CN"
    persona: str
    character_id: str | None = None


class AudioSegmentBinding(StrictModel):
    """Realization: where this utterance landed in the narration track."""

    file: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)


class Utterance(StrictModel):
    utterance_id: str
    anchor_ref: str
    placement: Literal[
        "full", "before_action", "during_action", "after_action"
    ] = "full"
    text: str = Field(min_length=1)
    delivery: DeliveryHint | None = None
    audio_segment: AudioSegmentBinding | None = None


class NarrationAudio(StrictModel):
    """Realization: the synthesized narration track."""

    audio_file: str
    duration_ms: int = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)


class AlignmentSummary(StrictModel):
    coverage: float = Field(ge=0, le=1)
    word_count: int = Field(ge=0)


class VoiceDesign(StrictModel):
    speaker: VoiceSpeakerSpec | None = None
    utterances: list[Utterance] = Field(default_factory=list)
    narration_audio: NarrationAudio | None = None
    alignment: AlignmentSummary | None = None

    @model_validator(mode="after")
    def validate_utterances(self) -> "VoiceDesign":
        ids = [item.utterance_id for item in self.utterances]
        if len(ids) != len(set(ids)):
            raise ValueError("utterance_id values must be unique")
        return self


# ---------------------------------------------------------------------------
# Shot layer: the universal currency
# ---------------------------------------------------------------------------


class ShotContinuity(StrictModel):
    """Generation-time join constraint, not an editing transition."""

    join: Literal["fresh", "continue_from_previous"] = "fresh"
    previous_shot_id: str | None = None

    @model_validator(mode="after")
    def require_previous_for_continuation(self) -> "ShotContinuity":
        if self.join == "continue_from_previous" and not self.previous_shot_id:
            raise ValueError("continue_from_previous requires previous_shot_id")
        if self.join == "fresh" and self.previous_shot_id is not None:
            raise ValueError("fresh shots must not carry previous_shot_id")
        return self


class ShotMedia(StrictModel):
    """Realization: the media this shot resolved to."""

    modality: Literal["ai_video", "composed"]
    local_path: str | None = None
    sha256: str | None = None
    duration_ms: int | None = Field(default=None, gt=0)
    generator_model: str | None = None
    generation_prompt: str | None = None
    generation_job_id: str | None = None
    generation_cost_usd: float | None = Field(default=None, ge=0)


class ShotDescription(StrictModel):
    spec: ProductionShot
    continuity: ShotContinuity = Field(default_factory=ShotContinuity)
    media: ShotMedia | None = None


# ---------------------------------------------------------------------------
# Timeline layer
# ---------------------------------------------------------------------------


class TransitionSpec(StrictModel):
    """Editing transition into a clip; meaningful for explainer only."""

    kind: Literal["cut", "crossfade", "wipe"] = "cut"
    duration_ms: int = Field(default=0, ge=0, le=2_000)


class ClipDescription(StrictModel):
    clip_id: str
    shot_ref: str
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    content_start_ms: int = Field(default=0, ge=0)
    content_end_ms: int | None = Field(default=None, gt=0)
    playback_path: str
    playback_modality: Literal["image", "video", "ai_video"]
    playback_policy: str | None = None
    audio_mode: Literal["narration_over", "embedded_in_video", "mixed"]
    source_asset_id: str | None = None
    derivative_id: str | None = None
    transition_in: TransitionSpec | None = None
    caption: str | None = None


class TimelineCaption(StrictModel):
    cue_id: str
    anchor_ref: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)


class TimelineOverlay(StrictModel):
    overlay_id: str
    anchor_ref: str
    overlay_type: Literal[
        "source_attribution",
        "generated_media_disclosure",
        "interpretation_label",
        "title_card",
        "step_progress",
    ]
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class TimelineDescription(StrictModel):
    canvas_width: int = 1080
    canvas_height: int = 1920
    fps: int = 30
    duration_ms: int = Field(gt=0)
    audio_file: str | None = None
    clips: list[ClipDescription] = Field(min_length=1)
    captions: list[TimelineCaption] = Field(default_factory=list)
    overlays: list[TimelineOverlay] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clip_sequence(self) -> "TimelineDescription":
        if self.clips[0].timeline_start_ms != 0:
            raise ValueError("timeline must start at zero")
        for current, following in zip(self.clips, self.clips[1:]):
            if current.timeline_end_ms != following.timeline_start_ms:
                raise ValueError("timeline clips must be contiguous")
        if self.clips[-1].timeline_end_ms != self.duration_ms:
            raise ValueError("timeline must cover its duration")
        return self


# ---------------------------------------------------------------------------
# Deliverable layer
# ---------------------------------------------------------------------------


class PublishMetadata(StrictModel):
    title_options: list[str] = Field(default_factory=list)
    description: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    cover_path: str | None = None


class Deliverable(StrictModel):
    publish: PublishMetadata = Field(default_factory=PublishMetadata)
    disclosures: list[str] = Field(default_factory=list)
    video_file: str | None = None
    sha256: str | None = None
    duration_ms: int | None = Field(default=None, gt=0)
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    renderer: str | None = None
    rendered_at: str | None = None


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class VideoDescription(StrictModel):
    """This document fully answers: what is this video?"""

    schema_version: str = "video-description.v1"
    intent: VideoIntent
    world: VideoWorld
    visual_language: VisualLanguage | None = None
    sound_design: SoundDesign | None = None
    structure: VideoStructure
    voice: VoiceDesign | None = None
    shots: list[ShotDescription] = Field(default_factory=list)
    timeline: TimelineDescription | None = None
    deliverable: Deliverable | None = None

    @model_validator(mode="after")
    def validate_format_alignment(self) -> "VideoDescription":
        if self.structure.kind != self.intent.format_id:
            raise ValueError("structure kind does not match intent.format_id")
        shot_ids = [shot.spec.shot_id for shot in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("shot_id values must be unique")
        known = set(shot_ids)
        for shot in self.shots:
            previous = shot.continuity.previous_shot_id
            if previous is not None and previous not in known:
                raise ValueError(
                    f"shot {shot.spec.shot_id} continues from unknown shot {previous}"
                )
        if self.timeline is not None:
            for clip in self.timeline.clips:
                prefix, _, shot_id = clip.shot_ref.partition(":")
                if prefix != "shot" or shot_id not in known:
                    raise ValueError(
                        f"clip {clip.clip_id} references unknown {clip.shot_ref}"
                    )
        return self


def element_refs(description: VideoDescription) -> list[str]:
    """Stable refs for every trackable description element, in document order.

    These refs key the execution-plane status tags and the dependency graph
    nodes, so the description document and the ledgers share one address space.
    """

    refs: list[str] = []
    structure = description.structure
    if isinstance(structure, ExplainerStructure):
        refs.extend(f"section:{item.section_id}" for item in structure.sections)
        refs.extend(f"beat:{item.planned_beat_id}" for item in structure.beats)
    elif isinstance(structure, DramaStructure):
        refs.extend(f"scene:{item.scene_id}" for item in structure.scenes)
        refs.extend(f"action:{item.action_id}" for item in structure.actions)
    else:
        refs.extend(f"step:{item.step_id}" for item in structure.steps)
        refs.extend(f"action:{item.action_id}" for item in structure.actions)
    if description.voice is not None:
        refs.extend(
            f"utterance:{item.utterance_id}" for item in description.voice.utterances
        )
    refs.extend(f"shot:{shot.spec.shot_id}" for shot in description.shots)
    if description.timeline is not None:
        refs.extend(f"clip:{clip.clip_id}" for clip in description.timeline.clips)
    return refs
