from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ...shared.models import StrictModel


ImageStrategy = Literal[
    "portrait_normalize",
    "subject_cover",
    "focus_crop",
    "contained_background",
]


class AssetInspection(StrictModel):
    asset_id: str
    analyzer_model: str
    content_type: Literal[
        "photo",
        "illustration",
        "chart",
        "document",
        "webpage",
        "meme",
        "other",
    ]
    focal_box: tuple[float, float, float, float]
    focus_confidence: float = Field(ge=0, le=1)
    preserve_full_frame: bool
    blocking_overlay: bool
    text_readability: Literal["none", "poor", "acceptable", "good"]
    key_text: list[str] = Field(default_factory=list)
    recommended_strategy: ImageStrategy
    reason: str

    @model_validator(mode="after")
    def validate_focal_box(self) -> "AssetInspection":
        x, y, width, height = self.focal_box
        if not all(0 <= value <= 1 for value in self.focal_box):
            raise ValueError("focal_box values must be normalized")
        if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise ValueError("focal_box must be inside the image")
        return self


class PreparedImage(StrictModel):
    processed_id: str
    asset_id: str
    beat_id: str
    source_path: str
    output_path: str
    output_mime_type: Literal["image/jpeg"] = "image/jpeg"
    sha256: str
    input_width: int = Field(gt=0)
    input_height: int = Field(gt=0)
    output_width: Literal[1080] = 1080
    output_height: Literal[1920] = 1920
    strategy: ImageStrategy
    upscaled: bool
    analysis: AssetInspection
