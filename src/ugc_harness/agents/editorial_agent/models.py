from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ...shared.models import StrictModel
from ...profiles.models import VideoProfileDecision


ClaimType = Literal[
    "factual",
    "interpretation",
    "opinion",
    "rhetorical",
]
VisualRole = Literal[
    "evidence",
    "illustration",
    "context",
    "explanation",
    "emotion",
    "contrast",
    "humor",
    "identity",
    "reset",
    "bridge",
    "reconstruction",
    "host_delivery",
]
AssetModality = Literal[
    "source_screenshot",
    "document_screenshot",
    "chart",
    "real_image",
    "meme",
    "kinetic_typography",
    "motion_graphic",
    "ai_image",
    "ai_video",
    "screen_recording",
    "talking_head",
]


class ClaimRecord(StrictModel):
    claim_id: str
    beat_id: str
    script_segment_ids: list[str] = Field(min_length=1)
    statement: str = Field(min_length=2)
    claim_type: ClaimType
    importance: float = Field(ge=0, le=1)
    interpretation_label_required: bool = False

    @model_validator(mode="after")
    def validate_claim_policy(self) -> "ClaimRecord":
        if self.claim_type == "interpretation" and not self.interpretation_label_required:
            raise ValueError("interpretations must require an explicit label")
        return self


class ExplorationDirection(StrictModel):
    direction_id: str
    order: int = Field(ge=1)
    description: str
    visual_role: VisualRole
    asset_type: AssetModality
    query: str | None = None
    covers_claim_ids: list[str] = Field(default_factory=list)
    grounding_requirement: Literal["none", "contextual", "source_exact"]
    generated_media_disclosure_required: bool = False
    must_not_imply: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_direction_policy(self) -> "ExplorationDirection":
        generated = {"ai_image", "ai_video"}
        is_generated = self.asset_type in generated
        if is_generated and not self.generated_media_disclosure_required:
            raise ValueError("generated media must require disclosure")
        if self.visual_role == "evidence":
            if not self.covers_claim_ids:
                raise ValueError("evidence directions must reference a claim")
            if self.grounding_requirement != "source_exact":
                raise ValueError("evidence directions must be source_exact")
            if is_generated:
                raise ValueError("AI media cannot be an evidence direction")
        return self


class VisualRequirement(StrictModel):
    visual_request_id: str
    beat_id: str
    purpose: str
    track: Literal["a_roll", "b_roll"] = "b_roll"
    speaker_visible: bool = False
    character_id: str | None = None
    selection_policy: Literal["first_success"] = "first_success"
    directions: list[ExplorationDirection] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_first_success_order(self) -> "VisualRequirement":
        ids = [direction.direction_id for direction in self.directions]
        if len(ids) != len(set(ids)):
            raise ValueError("direction_id values must be unique per requirement")
        orders = [direction.order for direction in self.directions]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError(
                "direction order must be contiguous, unique, and start at 1"
            )
        if self.track == "a_roll":
            if not self.speaker_visible or not self.character_id:
                raise ValueError("a_roll requires a visible, identified speaker")
            first = self.directions[0]
            if first.asset_type != "talking_head" or first.visual_role != "host_delivery":
                raise ValueError("a_roll must start with a talking_head host direction")
        elif self.speaker_visible or self.character_id:
            raise ValueError("b_roll cannot claim a visible host character")
        return self


class EditorialPlan(StrictModel):
    plan_version: str = "v3"
    project_id: str
    video_profile: VideoProfileDecision
    claims: list[ClaimRecord]
    visual_requirements: list[VisualRequirement] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "EditorialPlan":
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique")
        visual_ids = [
            request.visual_request_id for request in self.visual_requirements
        ]
        if len(visual_ids) != len(set(visual_ids)):
            raise ValueError("visual_request_id values must be unique")
        unknown_visual_claims = {
            claim_id
            for request in self.visual_requirements
            for direction in request.directions
            for claim_id in direction.covers_claim_ids
            if claim_id not in claim_ids
        }
        if unknown_visual_claims:
            raise ValueError(
                "unknown claim references: "
                f"{sorted(unknown_visual_claims)}"
            )
        return self


class EditorialQuality(StrictModel):
    passed: bool
    beat_visual_coverage: float = Field(ge=0, le=1)
    claim_count: int = Field(ge=0)
    factual_claim_count: int = Field(ge=0)
    interpretation_count: int = Field(ge=0)
    speaker_presence_ratio: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)


class EditorialArtifact(StrictModel):
    schema_version: str = "editorial.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model: str
    project_id: str
    source_narrative: str = "narrative_artifact.json"
    source_voice: str = "voice_artifact.json"
    editorial_plan: EditorialPlan
    quality: EditorialQuality
