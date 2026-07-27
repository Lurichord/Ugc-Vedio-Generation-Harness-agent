from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ..stage_one.models import StrictModel


ImageStrategy = Literal[
    "portrait_normalize",
    "subject_cover",
    "focus_crop",
    "contained_background",
]


class ImageAnalysis(StrictModel):
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
    def validate_focal_box(self) -> "ImageAnalysis":
        x, y, width, height = self.focal_box
        if not all(0 <= value <= 1 for value in self.focal_box):
            raise ValueError("focal_box values must be normalized")
        if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise ValueError("focal_box must be inside the image")
        return self


class ProcessedImage(StrictModel):
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
    analysis: ImageAnalysis


class RenderAssetMapping(StrictModel):
    clip_id: str
    asset_id: str
    original_path: str
    render_path: str


class ImagePreparationQuality(StrictModel):
    passed: bool
    eligible_image_count: int = Field(ge=0)
    processed_image_count: int = Field(ge=0)
    blocked_image_count: int = Field(ge=0)
    missing_output_count: int = Field(ge=0)
    low_resolution_input_count: int = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class ImagePreparationStageArtifact(StrictModel):
    schema_version: str = "image-preparation-stage.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project_id: str
    source_stage_two: str = "stage_two_artifact.json"
    source_stage_four: str = "stage_four_artifact.json"
    source_stage_five: str = "stage_five_artifact.json"
    processed_images: list[ProcessedImage]
    render_asset_mappings: list[RenderAssetMapping]
    quality: ImagePreparationQuality
