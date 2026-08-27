from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ...content import BeatRole
from ...shared.models import StrictModel


class VoiceSpeaker(StrictModel):
    provider: str
    voice_id: str
    language: str = "zh-CN"
    persona: str
    character_id: str | None = None
    gender: Literal["male", "female", "neutral"] | None = None
    age_style: Literal["young", "mature", "senior"] | None = None


class VoiceGlobalSettings(StrictModel):
    encoding: Literal["wav"] = "wav"
    sample_rate: int = 24_000
    channels: int = 1
    base_speed_ratio: float = Field(default=1.0, ge=0.5, le=2.0)
    volume_ratio: float = Field(default=1.0, ge=0.5, le=2.0)


class VoiceSegmentPlan(StrictModel):
    voice_segment_id: str
    script_segment_id: str
    planned_beat_id: str
    speech_act: BeatRole
    tone: str
    speed_ratio: float = Field(ge=0.5, le=2.0)
    energy: Literal["low", "medium", "high"]
    pause_before_ms: int = Field(ge=0, le=3_000)
    pause_after_ms: int = Field(ge=0, le=3_000)
    emphasis_words: list[str] = Field(default_factory=list)
    delivery_instruction: str


class VoicePlan(StrictModel):
    voice_plan_version: str = "v1"
    project_id: str
    speaker: VoiceSpeaker
    global_settings: VoiceGlobalSettings
    source_tones: list[str]
    segments: list[VoiceSegmentPlan] = Field(min_length=1)


class WordTimestamp(StrictModel):
    word_id: str
    word: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float | None = None
    script_segment_id: str
    planned_beat_id: str

    @model_validator(mode="after")
    def validate_time(self) -> "WordTimestamp":
        if self.end_ms <= self.start_ms:
            raise ValueError("word end_ms must be greater than start_ms")
        return self


class AudioSegment(StrictModel):
    audio_segment_id: str
    voice_segment_id: str
    script_segment_id: str
    planned_beat_id: str
    file: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    pause_before_ms: int = Field(ge=0)
    pause_after_ms: int = Field(ge=0)
    provider_request_id: str
    provider_log_id: str | None = None


class TimedAudio(StrictModel):
    audio_file: str
    duration_ms: int = Field(gt=0)
    encoding: Literal["wav"] = "wav"
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    sample_width_bytes: int = Field(gt=0)
    segments: list[AudioSegment] = Field(min_length=1)


class WordAlignment(StrictModel):
    source_audio: str
    basis: Literal["tts_native_timestamp"] = "tts_native_timestamp"
    normalized_text: str
    word_count: int = Field(ge=0)
    words: list[WordTimestamp]
    aligned_segment_count: int = Field(ge=0)
    total_segment_count: int = Field(gt=0)
    coverage: float = Field(ge=0, le=1)


class RealizedBeat(StrictModel):
    beat_id: str
    planned_beat_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    script_segment_ids: list[str] = Field(min_length=1)
    audio_segment_ids: list[str] = Field(min_length=1)
    narration: str
    proposition: str
    discourse_role: BeatRole
    relation_to_previous: str
    word_ids: list[str] = Field(default_factory=list)


class VoiceQuality(StrictModel):
    passed: bool
    audio_exists: bool
    audio_duration_ms: int
    segment_coverage: float = Field(ge=0, le=1)
    word_alignment_coverage: float = Field(ge=0, le=1)
    realized_beat_coverage: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)


class VoiceArtifact(StrictModel):
    schema_version: str = "voice.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project_id: str
    source_narrative: str
    voice_plan: VoicePlan
    timed_audio: TimedAudio
    word_alignment: WordAlignment
    realized_beats: list[RealizedBeat]
    quality: VoiceQuality
