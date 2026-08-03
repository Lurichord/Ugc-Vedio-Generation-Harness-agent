from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..shared.models import StrictModel


VideoProfileRequest = Literal["auto", "a_roll", "b_roll", "ab_roll"]
ResolvedVideoProfile = Literal["a_roll", "b_roll", "ab_roll"]


class VideoProfileDecision(StrictModel):
    requested: VideoProfileRequest
    resolved: ResolvedVideoProfile
    selection_source: Literal["user", "ai"]
    rationale: str
    speaker_presence_ratio_min: float = Field(ge=0, le=1)
    speaker_presence_ratio_max: float = Field(ge=0, le=1)
    character_consistency_required: bool
    character_id: str | None = None
    character_description: str | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> "VideoProfileDecision":
        if self.speaker_presence_ratio_min > self.speaker_presence_ratio_max:
            raise ValueError("speaker presence ratio range is reversed")
        if self.requested == "auto" and self.selection_source != "ai":
            raise ValueError("auto profile selection must come from ai")
        if self.requested != "auto":
            if self.selection_source != "user" or self.resolved != self.requested:
                raise ValueError("explicit profile selection must be preserved")
        if self.resolved in {"a_roll", "ab_roll"}:
            if not self.character_consistency_required:
                raise ValueError("speaker-led profiles require character consistency")
            if not self.character_id or not self.character_description:
                raise ValueError("speaker-led profiles require a character definition")
        return self
