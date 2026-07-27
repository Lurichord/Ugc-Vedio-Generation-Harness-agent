from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from ..stage_one.models import StrictModel


class RenderClip(StrictModel):
    clip_id: str
    beat_id: str
    start_frame: int = Field(ge=0)
    duration_in_frames: int = Field(gt=0)
    media_type: Literal["image", "video"]
    media_path: str
    source_path: str
    fit_mode: Literal["cover", "contain"]
    motion_preset: str
    scale_start: float
    scale_end: float
    transition_in: str


class RenderCaption(StrictModel):
    cue_id: str
    start_frame: int = Field(ge=0)
    duration_in_frames: int = Field(gt=0)
    text: str


class RenderOverlay(StrictModel):
    overlay_id: str
    overlay_type: str
    text: str
    start_frame: int = Field(ge=0)
    duration_in_frames: int = Field(gt=0)
    position: str


class RenderComposition(StrictModel):
    renderer: Literal["remotion"] = "remotion"
    renderer_version: str
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    fps: Literal[30] = 30
    duration_ms: int = Field(gt=0)
    duration_in_frames: int = Field(gt=0)
    audio_path: str
    clips: list[RenderClip] = Field(min_length=1)
    captions: list[RenderCaption]
    overlays: list[RenderOverlay]


class RenderedMedia(StrictModel):
    kind: Literal["final", "preview"]
    local_path: str
    mime_type: Literal["video/mp4"] = "video/mp4"
    sha256: str
    size_bytes: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    duration_ms: int = Field(gt=0)
    video_duration_ms: int = Field(gt=0)
    audio_duration_ms: int | None = Field(default=None, gt=0)
    container_duration_ms: int = Field(gt=0)
    video_codec: str
    audio_codec: str
    has_video: bool
    has_audio: bool


class RenderQuality(StrictModel):
    passed: bool
    expected_duration_ms: int = Field(gt=0)
    actual_duration_ms: int = Field(gt=0)
    duration_delta_ms: int = Field(ge=0)
    max_allowed_delta_ms: int = Field(gt=0)
    resolution_correct: bool
    fps_correct: bool
    audio_present: bool
    video_present: bool
    full_timeline_coverage: bool
    missing_media_count: int = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class RenderStageArtifact(StrictModel):
    schema_version: str = "render-stage.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project_id: str
    source_stage_two: str = "stage_two_artifact.json"
    source_stage_five: str = "stage_five_artifact.json"
    source_stage_seven: str = "stage_seven_artifact.json"
    composition: RenderComposition
    outputs: list[RenderedMedia]
    quality: RenderQuality
