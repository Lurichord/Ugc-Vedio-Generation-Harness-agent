from __future__ import annotations

from ..harness.models import CriticIssue, EvaluationResult
from ..content import DramaShotPayload, TutorialShotPayload
from ..agents.narrative_agent.models import (
    CreativeBrief,
    DramaPlanningArtifact,
    NarrativePlanningArtifact,
    NarrativeScriptArtifact,
    PlanningArtifact,
    QualityIssue,
    QualityReport,
    ScriptArtifact,
    ShotPlanArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)
from ..agents.narrative_agent.quality import evaluate


class NarrativeCritic:
    critic_id = "narrative_critic"

    def evaluate(
        self,
        brief: CreativeBrief,
        planning: NarrativePlanningArtifact,
        script: NarrativeScriptArtifact | None,
        target_ref: str,
        *,
        shots: ShotPlanArtifact | None = None,
    ) -> tuple[QualityReport, EvaluationResult]:
        if isinstance(planning, DramaPlanningArtifact):
            return self._evaluate_drama(brief, planning, shots, target_ref)
        if isinstance(planning, TutorialPlanningArtifact):
            if not isinstance(script, TutorialScriptArtifact):
                raise TypeError("tutorial critic requires TutorialScriptArtifact")
            return self._evaluate_tutorial(
                brief, planning, script, shots, target_ref
            )
        if not isinstance(planning, PlanningArtifact) or not isinstance(
            script,
            ScriptArtifact,
        ):
            raise TypeError("explainer critic requires PlanningArtifact and ScriptArtifact")
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

    def _evaluate_tutorial(
        self,
        brief: CreativeBrief,
        planning: TutorialPlanningArtifact,
        script: TutorialScriptArtifact,
        shots: ShotPlanArtifact | None,
        target_ref: str,
    ) -> tuple[QualityReport, EvaluationResult]:
        critic_id = "tutorial_critic"
        quality_issues: list[QualityIssue] = []
        step_ids = {item.step_id for item in planning.steps}
        covered_steps = {item.step_id for item in planning.actions}
        if missing := sorted(step_ids - covered_steps):
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="TUTORIAL_STEP_WITHOUT_ACTION",
                    message=f"这些制作步骤没有可观察动作：{missing}",
                )
            )
        for step in planning.steps:
            if not step.visual_evidence:
                quality_issues.append(
                    QualityIssue(
                        severity="error",
                        code="MISSING_VISUAL_EVIDENCE",
                        message=f"{step.step_id} 没有定义完成该步的视觉证据",
                        ref=step.step_id,
                    )
                )
        action_ids = {item.action_id for item in planning.actions}
        shot_action_ids: set[str] = set()
        if shots is None:
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="MISSING_TUTORIAL_SHOTS",
                    message="Tutorial candidate 缺少 ProductionShot",
                )
            )
        else:
            for shot in shots.shots:
                if isinstance(shot.payload, TutorialShotPayload):
                    shot_action_ids.update(shot.payload.action_ids)
                if (
                    shot.shot_kind != "tutorial"
                    or shot.visual.realization_type != "procedure_demo"
                    or shot.audio.audio_mode != "mixed"
                    or shot.timing.duration_driver != "demonstration_action"
                ):
                    quality_issues.append(
                        QualityIssue(
                            severity="error",
                            code="INVALID_TUTORIAL_REALIZATION",
                            message=(
                                f"{shot.shot_id} 必须使用 procedure_demo、mixed "
                                "和 demonstration_action"
                            ),
                            ref=shot.shot_id,
                        )
                    )
        if missing := sorted(action_ids - shot_action_ids):
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="MISSING_ACTION_SHOT",
                    message=f"这些制作动作没有对应 Shot：{missing}",
                )
            )
        total_ms = sum(item.target_duration_ms for item in planning.actions)
        if not brief.target.duration_target_ms * 0.7 <= total_ms <= (
            brief.target.duration_target_ms * 1.3
        ):
            quality_issues.append(
                QualityIssue(
                    severity="warning",
                    code="TUTORIAL_DURATION_OUT_OF_RANGE",
                    message=(
                        f"制作动作总时长 {total_ms}ms 与目标 "
                        f"{brief.target.duration_target_ms}ms 偏差超过 30%"
                    ),
                )
            )
        known_explanations = {
            item.explanation_segment_id for item in script.segments
        }
        used_explanations = {
            ref
            for shot in shots.shots if shots is not None
            for ref in getattr(shot.audio, "narration_segment_ids", [])
        } if shots is not None else set()
        if unknown := sorted(used_explanations - known_explanations):
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="UNKNOWN_EXPLANATION_REF",
                    message=f"Shot 引用了不存在的讲解片段：{unknown}",
                )
            )
        text_chars = sum(len(item.text.replace(" ", "")) for item in script.segments)
        coverage = len(action_ids & shot_action_ids) / len(action_ids) if action_ids else 0
        quality = QualityReport(
            passed=not any(item.severity == "error" for item in quality_issues),
            planned_duration_ms=total_ms,
            estimated_script_duration_ms=round(text_chars / 3.55 * 1000),
            script_char_count=text_chars,
            beat_coverage=round(coverage, 4),
            evidence_claim_count=sum(
                len(item.visual_evidence) for item in planning.steps
            ),
            issues=quality_issues,
        )
        issues = [
            CriticIssue(
                issue_id=f"{critic_id}:{index:03d}",
                critic_id=critic_id,
                scope="narrative",
                target_ref=target_ref,
                severity=item.severity,
                code=item.code,
                diagnosis=item.message,
                repair_options=["revise_tutorial_narrative"],
            )
            for index, item in enumerate(quality_issues, start=1)
        ]
        return quality, EvaluationResult(
            critic_id=critic_id,
            target_ref=target_ref,
            passed=quality.passed,
            issues=issues,
        )

    def _evaluate_drama(
        self,
        brief: CreativeBrief,
        planning: DramaPlanningArtifact,
        shots: ShotPlanArtifact | None,
        target_ref: str,
    ) -> tuple[QualityReport, EvaluationResult]:
        critic_id = "drama_critic"
        quality_issues: list[QualityIssue] = []
        scene_ids = {item.scene_id for item in planning.scenes}
        covered_scenes = {item.scene_id for item in planning.actions}
        if missing := sorted(scene_ids - covered_scenes):
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="DRAMA_SCENE_WITHOUT_ACTION",
                    message=f"这些场景没有可生成动作：{missing}",
                )
            )
        action_ids = {item.action_id for item in planning.actions}
        shot_action_ids: set[str] = set()
        if shots is None:
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="MISSING_DRAMA_SHOTS",
                    message="Drama candidate 缺少 ProductionShot",
                )
            )
        else:
            for shot in shots.shots:
                if isinstance(shot.payload, DramaShotPayload):
                    shot_action_ids.update(shot.payload.action_ids)
                if (
                    shot.shot_kind != "drama"
                    or shot.visual.realization_type != "generated_scene"
                    or shot.audio.audio_mode != "embedded_in_video"
                    or shot.timing.duration_driver != "generated_clip"
                ):
                    quality_issues.append(
                        QualityIssue(
                            severity="error",
                            code="INVALID_DRAMA_REALIZATION",
                            message=(
                                f"{shot.shot_id} 必须使用 generated_scene、"
                                "embedded_in_video 和 generated_clip"
                            ),
                            ref=shot.shot_id,
                        )
                    )
        if missing := sorted(action_ids - shot_action_ids):
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="MISSING_ACTION_SHOT",
                    message=f"这些表演动作没有对应 Shot：{missing}",
                )
            )
        total_ms = sum(item.target_duration_ms for item in planning.actions)
        if not brief.target.duration_target_ms * 0.75 <= total_ms <= (
            brief.target.duration_target_ms * 1.25
        ):
            quality_issues.append(
                QualityIssue(
                    severity="warning",
                    code="DRAMA_DURATION_OUT_OF_RANGE",
                    message=(
                        f"剧情动作总时长 {total_ms}ms 与目标"
                        f" {brief.target.duration_target_ms}ms 偏差超过 25%"
                    ),
                )
            )
        if planning.video_profile.resolved != "b_roll":
            quality_issues.append(
                QualityIssue(
                    severity="error",
                    code="DRAMA_HOST_PROFILE",
                    message="Drama 不应使用主持人口播型 video profile",
                )
            )
        dialogue_chars = sum(
            len(line.replace(" ", ""))
            for action in planning.actions
            for line in action.dialogue_lines
        )
        coverage = (
            len(action_ids & shot_action_ids) / len(action_ids)
            if action_ids
            else 0.0
        )
        quality = QualityReport(
            passed=not any(
                item.severity == "error" for item in quality_issues
            ),
            planned_duration_ms=total_ms,
            estimated_script_duration_ms=total_ms,
            script_char_count=dialogue_chars,
            beat_coverage=round(coverage, 4),
            evidence_claim_count=0,
            issues=quality_issues,
        )
        issues = [
            CriticIssue(
                issue_id=f"{critic_id}:{index:03d}",
                critic_id=critic_id,
                scope="narrative",
                target_ref=target_ref,
                severity=item.severity,
                code=item.code,
                diagnosis=item.message,
                repair_options=["revise_drama_narrative"],
            )
            for index, item in enumerate(quality_issues, start=1)
        ]
        return quality, EvaluationResult(
            critic_id=critic_id,
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
