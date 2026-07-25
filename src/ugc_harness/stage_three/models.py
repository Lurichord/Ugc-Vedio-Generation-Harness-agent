from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ..stage_one.models import StrictModel


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
]
AssetModality = Literal[
    "source_screenshot",
    "document_screenshot",
    "chart",
    "real_video",
    "real_image",
    "meme",
    "kinetic_typography",
    "motion_graphic",
    "ai_image",
    "ai_video",
    "talking_head",
]


class ClaimRecord(StrictModel):
    claim_id: str
    beat_id: str
    script_segment_ids: list[str] = Field(min_length=1)
    statement: str = Field(min_length=2)
    claim_type: ClaimType
    importance: float = Field(ge=0, le=1)
    evidence_required: bool
    interpretation_label_required: bool = False
    source_status: Literal["research_required", "not_applicable"]
    if_unsupported: Literal[
        "retain_as_opinion",
        "reframe_as_interpretation",
        "modify_or_remove",
    ]

    @model_validator(mode="after")
    def validate_claim_policy(self) -> "ClaimRecord":
        if self.claim_type == "factual":
            if not self.evidence_required:
                raise ValueError("factual claims must require evidence")
            if self.source_status != "research_required":
                raise ValueError("factual claims must be marked research_required")
        if self.claim_type == "interpretation" and not self.interpretation_label_required:
            raise ValueError("interpretations must require an explicit label")
        return self


class EvidenceRequest(StrictModel):
    evidence_request_id: str
    claim_id: str
    claim_to_verify: str
    search_queries: list[str] = Field(min_length=2, max_length=6)
    acceptable_source_types: list[str] = Field(min_length=1)
    preferred_publishers: list[str] = Field(default_factory=list)
    verification_questions: list[str] = Field(min_length=1)
    visual_evidence_desired: bool = False
    direct_visual_evidence_required: bool = False


class VisualRequirement(StrictModel):
    visual_request_id: str
    beat_id: str
    primary_role: VisualRole
    supporting_roles: list[VisualRole] = Field(default_factory=list)
    purpose: str
    content_description: str
    preferred_modalities: list[AssetModality] = Field(min_length=1)
    search_queries: list[str] = Field(default_factory=list, max_length=6)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    grounding_requirement: Literal["none", "contextual", "source_exact"]
    generated_media_allowed: bool
    generated_media_can_satisfy_evidence: Literal[False] = False
    generated_media_disclosure_required: bool = False
    must_not_imply: list[str] = Field(default_factory=list)
    fallback_ladder: list[str] = Field(min_length=2)
    max_asset_count: int = Field(default=2, ge=1, le=4)

    @model_validator(mode="after")
    def validate_visual_policy(self) -> "VisualRequirement":
        generated = {"ai_image", "ai_video"}
        has_generated_modality = bool(generated.intersection(self.preferred_modalities))
        if has_generated_modality and not self.generated_media_allowed:
            raise ValueError("AI modality requires generated_media_allowed=true")
        if self.generated_media_allowed and not self.generated_media_disclosure_required:
            raise ValueError("generated media must require disclosure")
        if self.primary_role == "evidence":
            if not self.evidence_claim_ids:
                raise ValueError("evidence visuals must reference a claim")
            if self.grounding_requirement != "source_exact":
                raise ValueError("evidence visuals must be source_exact")
            if has_generated_modality:
                raise ValueError(
                    "AI media cannot be a preferred modality for evidence visuals"
                )
        return self


class EditorialPlan(StrictModel):
    plan_version: str = "v1"
    project_id: str
    claims: list[ClaimRecord]
    evidence_requests: list[EvidenceRequest]
    visual_requirements: list[VisualRequirement] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "EditorialPlan":
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique")
        evidence_ids = [
            request.evidence_request_id for request in self.evidence_requests
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_request_id values must be unique")
        visual_ids = [
            request.visual_request_id for request in self.visual_requirements
        ]
        if len(visual_ids) != len(set(visual_ids)):
            raise ValueError("visual_request_id values must be unique")
        unknown_evidence_claims = {
            request.claim_id
            for request in self.evidence_requests
            if request.claim_id not in claim_ids
        }
        unknown_visual_claims = {
            claim_id
            for request in self.visual_requirements
            for claim_id in request.evidence_claim_ids
            if claim_id not in claim_ids
        }
        if unknown_evidence_claims or unknown_visual_claims:
            raise ValueError(
                "unknown claim references: "
                f"{sorted(unknown_evidence_claims | unknown_visual_claims)}"
            )
        return self


class EditorialQuality(StrictModel):
    passed: bool
    beat_visual_coverage: float = Field(ge=0, le=1)
    factual_evidence_request_coverage: float = Field(ge=0, le=1)
    planned_evidence_beat_coverage: float = Field(ge=0, le=1)
    claim_count: int = Field(ge=0)
    factual_claim_count: int = Field(ge=0)
    interpretation_count: int = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class EditorialStageArtifact(StrictModel):
    schema_version: str = "editorial-stage.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model: str
    project_id: str
    source_stage_one: str = "stage_one_artifact.json"
    source_stage_two: str = "stage_two_artifact.json"
    editorial_plan: EditorialPlan
    quality: EditorialQuality
