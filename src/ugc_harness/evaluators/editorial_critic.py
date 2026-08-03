from __future__ import annotations

from ..agents.editorial_agent.models import (
    EditorialPlan,
    EditorialQuality,
)
from ..agents.narrative_agent.models import NarrativeArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..harness.models import CriticIssue, EvaluationResult


class EditorialCritic:
    critic_id = "editorial_critic"

    def evaluate(
        self,
        narrative: NarrativeArtifact,
        voice: VoiceArtifact,
        plan: EditorialPlan,
        target_ref: str,
    ) -> tuple[EditorialQuality, EvaluationResult]:
        quality = evaluate_editorial_plan(narrative, voice, plan)
        issues = [
            CriticIssue(
                issue_id=f"{self.critic_id}:{index:03d}",
                critic_id=self.critic_id,
                scope="editorial",
                target_ref=target_ref,
                severity="error",
                code=_issue_code(message),
                diagnosis=message,
                repair_options=["revise_editorial_plan"],
            )
            for index, message in enumerate(quality.issues, start=1)
        ]
        return quality, EvaluationResult(
            critic_id=self.critic_id,
            target_ref=target_ref,
            passed=quality.passed,
            issues=issues,
        )


def evaluate_editorial_plan(
    narrative: NarrativeArtifact,
    voice: VoiceArtifact,
    plan: EditorialPlan,
) -> EditorialQuality:
    issues: list[str] = []
    beat_ids = {beat.beat_id for beat in voice.realized_beats}
    visual_beat_ids = [item.beat_id for item in plan.visual_requirements]
    unknown_beats = {
        claim.beat_id for claim in plan.claims if claim.beat_id not in beat_ids
    } | {beat_id for beat_id in visual_beat_ids if beat_id not in beat_ids}
    if unknown_beats:
        issues.append(f"引用了不存在的 RealizedBeat: {sorted(unknown_beats)}")
    missing_visuals = beat_ids - set(visual_beat_ids)
    duplicate_visuals = {
        beat_id for beat_id in visual_beat_ids if visual_beat_ids.count(beat_id) > 1
    }
    if missing_visuals:
        issues.append(f"这些 Beat 缺少视觉需求: {sorted(missing_visuals)}")
    if duplicate_visuals:
        issues.append(f"这些 Beat 有重复视觉需求: {sorted(duplicate_visuals)}")

    factual_claims = [item for item in plan.claims if item.claim_type == "factual"]
    factual_ids = {item.claim_id for item in factual_claims}
    evidence_claims = {
        claim_id
        for visual in plan.visual_requirements
        for direction in visual.directions
        if direction.visual_role == "evidence"
        for claim_id in direction.covers_claim_ids
    }
    invalid_evidence = evidence_claims - factual_ids
    if invalid_evidence:
        issues.append(f"证据画面引用了非事实主张: {sorted(invalid_evidence)}")

    expected_profile = narrative.planning.video_profile
    if plan.video_profile != expected_profile:
        issues.append("EditorialPlan 修改了已批准的 video_profile")
    requirements = {item.beat_id: item for item in plan.visual_requirements}
    total_ms = sum(item.duration_ms for item in voice.realized_beats)
    speaker_ms = sum(
        beat.duration_ms
        for beat in voice.realized_beats
        if requirements.get(beat.beat_id)
        and requirements[beat.beat_id].track == "a_roll"
    )
    speaker_ratio = speaker_ms / total_ms if total_ms else 0.0
    if not (
        expected_profile.speaker_presence_ratio_min
        <= speaker_ratio
        <= expected_profile.speaker_presence_ratio_max
    ):
        issues.append(
            "人物出镜占比不符合 video_profile："
            f"{speaker_ratio:.3f} 不在 "
            f"{expected_profile.speaker_presence_ratio_min:.3f}–"
            f"{expected_profile.speaker_presence_ratio_max:.3f}"
        )
    if expected_profile.character_consistency_required and any(
        item.track == "a_roll" and item.character_id != expected_profile.character_id
        for item in plan.visual_requirements
    ):
        issues.append("A-roll 使用了不一致的人物标识")

    coverage = (
        len(beat_ids.intersection(visual_beat_ids)) / len(beat_ids)
        if beat_ids
        else 1.0
    )
    return EditorialQuality(
        passed=not issues,
        beat_visual_coverage=round(coverage, 4),
        claim_count=len(plan.claims),
        factual_claim_count=len(factual_claims),
        interpretation_count=sum(
            item.claim_type == "interpretation" for item in plan.claims
        ),
        speaker_presence_ratio=round(speaker_ratio, 4),
        issues=issues,
    )


def _issue_code(message: str) -> str:
    if "video_profile" in message or "人物" in message or "A-roll" in message:
        return "VIDEO_PROFILE_VIOLATION"
    if "缺少视觉" in message or "重复视觉" in message:
        return "VISUAL_COVERAGE_INVALID"
    if "证据" in message:
        return "EVIDENCE_POLICY_INVALID"
    return "EDITORIAL_PLAN_INVALID"
