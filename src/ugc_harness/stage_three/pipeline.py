from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from .models import (
    EditorialPlan,
    EditorialQuality,
    EditorialStageArtifact,
)
from .prompts import editorial_plan_prompt, editorial_repair_prompt
from ..stage_one.models import StageOneArtifact
from ..stage_two.models import VoiceStageArtifact

T = TypeVar("T", bound=BaseModel)


class JSONGenerator(Protocol):
    settings: object

    def generate(self, prompt: str, output_type: type[T]) -> T: ...


class EditorialStagePipeline:
    def __init__(self, generator: JSONGenerator, model_name: str):
        self.generator = generator
        self.model_name = model_name

    def run(
        self,
        stage_one: StageOneArtifact,
        stage_two: VoiceStageArtifact,
    ) -> EditorialStageArtifact:
        if stage_one.brief.project_id != stage_two.project_id:
            raise ValueError("stage one and stage two project_id do not match")

        plan = self.generator.generate(
            editorial_plan_prompt(stage_one, stage_two),
            EditorialPlan,
        )
        quality = evaluate_editorial_plan(stage_one, stage_two, plan)
        if not quality.passed:
            plan = self.generator.generate(
                editorial_repair_prompt(
                    stage_one,
                    stage_two,
                    plan.model_dump_json(indent=2),
                    quality.issues,
                ),
                EditorialPlan,
            )
            quality = evaluate_editorial_plan(stage_one, stage_two, plan)

        return EditorialStageArtifact(
            model=self.model_name,
            project_id=stage_one.brief.project_id,
            editorial_plan=plan,
            quality=quality,
        )


def evaluate_editorial_plan(
    stage_one: StageOneArtifact,
    stage_two: VoiceStageArtifact,
    plan: EditorialPlan,
) -> EditorialQuality:
    issues: list[str] = []
    beat_ids = {beat.beat_id for beat in stage_two.realized_beats}
    visual_beat_ids = [item.beat_id for item in plan.visual_requirements]
    unknown_beats = {
        claim.beat_id for claim in plan.claims if claim.beat_id not in beat_ids
    } | {beat_id for beat_id in visual_beat_ids if beat_id not in beat_ids}
    if unknown_beats:
        issues.append(f"引用了不存在的 RealizedBeat: {sorted(unknown_beats)}")

    missing_visuals = beat_ids - set(visual_beat_ids)
    duplicate_visuals = {
        beat_id
        for beat_id in visual_beat_ids
        if visual_beat_ids.count(beat_id) > 1
    }
    if missing_visuals:
        issues.append(f"这些 Beat 缺少视觉需求: {sorted(missing_visuals)}")
    if duplicate_visuals:
        issues.append(f"这些 Beat 有重复视觉需求: {sorted(duplicate_visuals)}")

    factual_claims = [
        claim for claim in plan.claims if claim.claim_type == "factual"
    ]
    requested_claim_ids = {
        request.claim_id for request in plan.evidence_requests
    }
    missing_evidence = {
        claim.claim_id
        for claim in factual_claims
        if claim.claim_id not in requested_claim_ids
    }
    if missing_evidence:
        issues.append(f"事实主张缺少 EvidenceRequest: {sorted(missing_evidence)}")

    planned_evidence_beat_ids = {
        realized.beat_id
        for realized in stage_two.realized_beats
        for planned in stage_one.planning.beats
        if realized.planned_beat_id == planned.planned_beat_id
        and planned.evidence_need.required
    }
    factual_beat_ids = {claim.beat_id for claim in factual_claims}
    missing_planned_claims = planned_evidence_beat_ids - factual_beat_ids
    if missing_planned_claims:
        issues.append(
            "第一阶段要求核实、但未抽取为 factual 的 Beat: "
            f"{sorted(missing_planned_claims)}"
        )

    evidence_visual_claims = {
        claim_id
        for visual in plan.visual_requirements
        if visual.primary_role == "evidence"
        for claim_id in visual.evidence_claim_ids
    }
    factual_ids = {claim.claim_id for claim in factual_claims}
    invalid_evidence_visuals = evidence_visual_claims - factual_ids
    if invalid_evidence_visuals:
        issues.append(
            "证据画面引用了非事实主张: "
            f"{sorted(invalid_evidence_visuals)}"
        )

    beat_visual_coverage = (
        len(beat_ids.intersection(visual_beat_ids)) / len(beat_ids)
        if beat_ids
        else 1.0
    )
    evidence_coverage = (
        len(factual_ids.intersection(requested_claim_ids)) / len(factual_ids)
        if factual_ids
        else 1.0
    )
    planned_evidence_coverage = (
        len(planned_evidence_beat_ids.intersection(factual_beat_ids))
        / len(planned_evidence_beat_ids)
        if planned_evidence_beat_ids
        else 1.0
    )
    return EditorialQuality(
        passed=not issues,
        beat_visual_coverage=round(beat_visual_coverage, 4),
        factual_evidence_request_coverage=round(evidence_coverage, 4),
        planned_evidence_beat_coverage=round(
            planned_evidence_coverage, 4
        ),
        claim_count=len(plan.claims),
        factual_claim_count=len(factual_claims),
        interpretation_count=sum(
            claim.claim_type == "interpretation" for claim in plan.claims
        ),
        issues=issues,
    )
