from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ...shared.models import StrictModel
from ..editorial_agent.models import AssetModality

VIDEO_FILE_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})


class SourceTrace(StrictModel):
    source_url: str
    title: str | None = None
    publisher: str | None = None
    retrieved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    verification_status: Literal["not_evaluated"] = "not_evaluated"
    rights_status: Literal["unknown", "citation_only", "permitted"] = "unknown"


class AssetUsabilityReview(StrictModel):
    reviewer_model: str
    usable: bool
    login_or_auth_overlay: bool
    obstruction_level: Literal["none", "minor", "major", "full"]
    reason: str


class AssetCard(StrictModel):
    asset_id: str
    visual_request_id: str
    direction_id: str
    beat_id: str
    modality: AssetModality
    origin: Literal["downloaded", "captured", "generated", "rendered"]
    local_path: str
    mime_type: str
    sha256: str
    source: SourceTrace | None = None
    generated_media_disclosure_required: bool = False
    generator_model: str | None = None
    generation_prompt: str | None = None
    generation_job_id: str | None = None
    generation_cost_usd: float | None = Field(default=None, ge=0)
    usability_review: AssetUsabilityReview | None = None
    production_ready: bool = True
    character_id: str | None = None
    continuity_group_id: str | None = None
    previous_asset_id: str | None = None
    identity_reference_path: str | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> "AssetCard":
        if self.origin == "generated":
            if not self.generated_media_disclosure_required:
                raise ValueError("generated media must require disclosure")
            if not self.generator_model or not self.generation_prompt:
                raise ValueError("generated media must record model and prompt")
            if self.source is not None:
                raise ValueError("generated media cannot have a factual source")
        if self.origin in {"downloaded", "captured"}:
            if self.usability_review is None:
                raise ValueError("web media must include a usability review")
            if not self.usability_review.usable:
                raise ValueError("unusable web media cannot become an AssetCard")
        return self


def is_talking_head_video(
    asset: "AssetCard",
    resolved_path: Path | None = None,
) -> bool:
    """Whether the asset that was actually produced is a talking-head video.

    Editorial planning metadata may say "a_roll", but a failed talking-head
    direction can fall back to a static image. Frame-continuity chaining must
    only ever extract frames from a real talking-head video, so callers check
    the produced asset itself instead of trusting the plan.
    """
    if asset.modality != "talking_head":
        return False
    looks_like_video = asset.mime_type.lower().startswith("video/") or (
        Path(asset.local_path).suffix.lower() in VIDEO_FILE_SUFFIXES
    )
    if not looks_like_video:
        return False
    if resolved_path is not None and not (
        resolved_path.is_file() and resolved_path.stat().st_size
    ):
        return False
    return True


class DirectionAttempt(StrictModel):
    direction_id: str
    order: int = Field(ge=1)
    status: Literal["success", "not_found", "not_supported", "error"]
    reason: str


class VisualResolution(StrictModel):
    visual_request_id: str
    beat_id: str
    status: Literal["resolved", "unresolved"]
    selected_direction_id: str | None = None
    asset_id: str | None = None
    attempts: list[DirectionAttempt] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_resolution(self) -> "VisualResolution":
        successes = [
            attempt for attempt in self.attempts if attempt.status == "success"
        ]
        if self.status == "resolved":
            if len(successes) != 1 or not self.asset_id:
                raise ValueError(
                    "resolved visual must contain exactly one success and asset_id"
                )
            if self.selected_direction_id != successes[0].direction_id:
                raise ValueError("selected direction must be the successful attempt")
            if self.attempts[-1].status != "success":
                raise ValueError("first-success execution must stop after success")
        elif successes or self.asset_id or self.selected_direction_id:
            raise ValueError("unresolved visual cannot reference a selected asset")
        return self


class AssetQuality(StrictModel):
    passed: bool
    visual_request_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    resolution_coverage: float = Field(ge=0, le=1)
    first_success_violations: int = Field(ge=0)
    inspected_image_count: int = Field(default=0, ge=0)
    prepared_image_count: int = Field(default=0, ge=0)
    blocked_image_count: int = Field(default=0, ge=0)
    issues: list[str] = Field(default_factory=list)


class AssetArtifact(StrictModel):
    schema_version: str = "asset.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project_id: str
    source_editorial: str = "editorial_artifact.json"
    assets: list[AssetCard]
    resolutions: list[VisualResolution]
    inspections: list["AssetInspection"] = Field(default_factory=list)
    prepared_images: list["PreparedImage"] = Field(default_factory=list)
    quality: AssetQuality


from .image_models import AssetInspection, PreparedImage

AssetArtifact.model_rebuild()


class AssetCandidate(StrictModel):
    """Uncommitted asset stage output submitted by the unified agent."""

    assets: list[AssetCard]
    resolutions: list[VisualResolution]
    inspections: list[AssetInspection] = Field(default_factory=list)
    prepared_images: list[PreparedImage] = Field(default_factory=list)
