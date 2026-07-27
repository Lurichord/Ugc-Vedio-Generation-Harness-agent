from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ..stage_one.models import StrictModel


class DerivedAsset(StrictModel):
    derivative_id: str
    source_asset_id: str
    derivation_type: Literal["ai_image_to_video"]
    local_path: str
    mime_type: str
    sha256: str
    generator_model: str
    generation_prompt: str
    generation_job_id: str
    generation_cost_usd: float | None = Field(default=None, ge=0)
    generated_media_disclosure_required: Literal[True] = True


class TimelineClip(StrictModel):
    clip_id: str
    beat_id: str
    visual_request_id: str
    source_asset_id: str
    derivative_id: str | None = None
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    content_start_ms: int = Field(ge=0)
    content_end_ms: int = Field(gt=0)
    playback_path: str
    playback_modality: Literal["image", "video"]
    playback_policy: Literal[
        "hold_to_audio",
        "trim_or_loop_to_audio",
    ]
    transition_in: Literal["none", "hard_cut", "punch_cut"]

    @model_validator(mode="after")
    def validate_times(self) -> "TimelineClip":
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("clip timeline end must be after start")
        if self.content_end_ms <= self.content_start_ms:
            raise ValueError("clip content end must be after start")
        if not (
            self.timeline_start_ms
            <= self.content_start_ms
            < self.content_end_ms
            <= self.timeline_end_ms
        ):
            raise ValueError("content interval must be inside clip interval")
        return self


class CaptionCue(StrictModel):
    cue_id: str
    beat_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    word_ids: list[str] = Field(min_length=1)
    style: Literal["ugc_primary"] = "ugc_primary"


class VisualTransform(StrictModel):
    clip_id: str
    fit_mode: Literal["cover", "contain"]
    motion_preset: Literal[
        "subtle_push",
        "slow_pan",
        "document_focus",
        "native_video",
        "ai_screen_motion",
    ]
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    scale_start: float = Field(ge=0.5, le=2)
    scale_end: float = Field(ge=0.5, le=2)
    safe_area_percent: float = Field(default=0.08, ge=0, le=0.25)


class OverlayCue(StrictModel):
    overlay_id: str
    beat_id: str
    overlay_type: Literal[
        "source_attribution",
        "generated_media_disclosure",
        "interpretation_label",
    ]
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    position: Literal["top_left", "top_right", "bottom_left"]


class TimelinePlan(StrictModel):
    canvas_width: Literal[1080] = 1080
    canvas_height: Literal[1920] = 1920
    fps: Literal[30] = 30
    audio_file: str
    duration_ms: int = Field(gt=0)
    clips: list[TimelineClip] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_clip_sequence(self) -> "TimelinePlan":
        if self.clips[0].timeline_start_ms != 0:
            raise ValueError("timeline must start at 0")
        for current, following in zip(self.clips, self.clips[1:]):
            if current.timeline_end_ms != following.timeline_start_ms:
                raise ValueError("timeline clips must be contiguous")
        if self.clips[-1].timeline_end_ms != self.duration_ms:
            raise ValueError("timeline clips must cover the full audio duration")
        return self


class TimelineQuality(StrictModel):
    passed: bool
    beat_count: int = Field(ge=0)
    clip_count: int = Field(ge=0)
    caption_cue_count: int = Field(ge=0)
    full_audio_coverage: bool
    missing_asset_count: int = Field(ge=0)
    screen_derivative_count: int = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class TimelineStageArtifact(StrictModel):
    schema_version: str = "timeline-stage.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project_id: str
    source_stage_two: str = "stage_two_artifact.json"
    source_stage_three: str = "stage_three_artifact.json"
    source_stage_four: str = "stage_four_artifact.json"
    derivatives: list[DerivedAsset]
    timeline: TimelinePlan
    captions: list[CaptionCue]
    visual_transforms: list[VisualTransform]
    overlays: list[OverlayCue]
    quality: TimelineQuality
