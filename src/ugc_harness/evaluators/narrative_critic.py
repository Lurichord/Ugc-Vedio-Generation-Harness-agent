from __future__ import annotations

from ..harness.models import CriticIssue, EvaluationResult
from ..agents.narrative_agent.models import (
    CreativeBrief,
    PlanningArtifact,
    QualityIssue,
    QualityReport,
    ScriptArtifact,
)
from ..agents.narrative_agent.quality import evaluate


class NarrativeCritic:
    critic_id = "narrative_critic"

    def evaluate(
        self,
        brief: CreativeBrief,
        planning: PlanningArtifact,
        script: ScriptArtifact,
        target_ref: str,
    ) -> tuple[QualityReport, EvaluationResult]:
        quality = evaluate(brief, planning, script)
        profile = planning.video_profile
        if profile.requested != brief.video_profile or (
            brief.video_profile != "auto"
            and profile.resolved != brief.video_profile
        ):
            quality.issues.append(
                QualityIssue(
                    severity="error",
                    code="VIDEO_PROFILE_MISMATCH",
                    message="规划结果没有遵守用户指定的 video profile",
                )
            )
            quality.passed = False
        issues = [
            CriticIssue(
                issue_id=f"{self.critic_id}:{index:03d}",
                critic_id=self.critic_id,
                scope="narrative",
                target_ref=target_ref,
                severity=issue.severity,
                code=issue.code,
                diagnosis=issue.message,
                repair_options=_repair_options(issue.code),
            )
            for index, issue in enumerate(quality.issues, start=1)
        ]
        return quality, EvaluationResult(
            critic_id=self.critic_id,
            target_ref=target_ref,
            passed=quality.passed,
            issues=issues,
        )


def _repair_options(code: str) -> list[str]:
    if code in {"WEAK_HOOK", "CLOSE_WITHOUT_PAYOFF"}:
        return ["revise_section"]
    if code in {"MISSING_BEAT_SCRIPT", "SPEECH_ACT_MISMATCH"}:
        return ["revise_beat_script"]
    if code == "SCRIPT_DURATION_OUT_OF_RANGE":
        return ["rebalance_script"]
    return ["revise_narrative"]
