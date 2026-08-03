from __future__ import annotations

import re

from .models import (
    CreativeBrief,
    PlannedBeat,
    PlanningArtifact,
    QualityIssue,
    QualityReport,
    ScriptArtifact,
)


def visible_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def estimate_duration_ms(script: ScriptArtifact) -> int:
    # 这里只做宽松预估；真实时长由后续 tone、配音和词级对齐决定。
    chars = sum(visible_char_count(segment.text) for segment in script.segments)
    pauses = sum(
        segment.delivery_hint.pause_before_ms + segment.delivery_hint.pause_after_ms
        for segment in script.segments
    )
    return round(chars / 4.0 * 1000 + pauses)


def evaluate(
    brief: CreativeBrief, planning: PlanningArtifact, script: ScriptArtifact
) -> QualityReport:
    issues: list[QualityIssue] = []
    planned_ms = sum(beat.target_duration_ms for beat in planning.beats)
    estimated_ms = estimate_duration_ms(script)
    char_count = sum(visible_char_count(segment.text) for segment in script.segments)

    beat_ids = {beat.planned_beat_id for beat in planning.beats}
    covered_ids = {segment.planned_beat_id for segment in script.segments}
    unknown_ids = covered_ids - beat_ids
    missing_ids = beat_ids - covered_ids
    coverage = len(covered_ids & beat_ids) / len(beat_ids)

    if unknown_ids:
        issues.append(
            QualityIssue(
                severity="error",
                code="UNKNOWN_BEAT_REF",
                message=f"剧本引用未知 Beat：{sorted(unknown_ids)}",
            )
        )
    if missing_ids:
        issues.append(
            QualityIssue(
                severity="error",
                code="MISSING_BEAT_SCRIPT",
                message=f"以下 Beat 没有口播：{sorted(missing_ids)}",
            )
        )
    # 不把 planned duration 与 target 的偏差视为错误，也不做自动归一化。
    # Narrative Critic 只有在明显超出产品的 1–2 分钟范围时才提示。
    if not 60_000 <= estimated_ms <= 120_000:
        issues.append(
            QualityIssue(
                severity="warning",
                code="SCRIPT_DURATION_OUT_OF_RANGE",
                message=(
                    f"估算口播 {estimated_ms / 1000:.1f}s，"
                    "宽松窗口为 60–120s；真实时长以后续配音对齐为准"
                ),
            )
        )

    by_id: dict[str, PlannedBeat] = {
        beat.planned_beat_id: beat for beat in planning.beats
    }
    for segment in script.segments:
        beat = by_id.get(segment.planned_beat_id)
        if beat and segment.delivery_hint.speech_act != beat.discourse_role:
            issues.append(
                QualityIssue(
                    severity="error",
                    code="SPEECH_ACT_MISMATCH",
                    message=(
                        f"{segment.script_segment_id} 的 speech_act 与 "
                        f"{beat.planned_beat_id} 的 discourse_role 不一致"
                    ),
                    ref=segment.script_segment_id,
                )
            )
        missing_emphasis = [
            word
            for word in segment.delivery_hint.emphasis_words
            if word not in segment.text
        ]
        if missing_emphasis:
            issues.append(
                QualityIssue(
                    severity="warning",
                    code="EMPHASIS_NOT_IN_TEXT",
                    message=f"重音词未出现在正文：{missing_emphasis}",
                    ref=segment.script_segment_id,
                )
            )

    section_roles = [section.role for section in planning.sections]
    if section_roles != ["hook", "body", "close"]:
        issues.append(
            QualityIssue(
                severity="error",
                code="INVALID_SECTION_ORDER",
                message="Section 必须依次为 hook、body、close",
            )
        )
    first_roles = [beat.discourse_role for beat in planning.beats[:2]]
    if not any(role in {"question", "reveal", "contrast"} for role in first_roles):
        issues.append(
            QualityIssue(
                severity="warning",
                code="WEAK_HOOK",
                message="前两个 Beat 没有明显的信息缺口、揭示或反差",
            )
        )
    if not any(
        beat.discourse_role in {"payoff", "callback"}
        and beat.section_id == planning.sections[-1].section_id
        for beat in planning.beats
    ):
        issues.append(
            QualityIssue(
                severity="warning",
                code="NO_HOOK_PAYOFF",
                message="Close 中缺少 payoff 或 callback Beat",
            )
        )

    return QualityReport(
        passed=not any(issue.severity == "error" for issue in issues),
        planned_duration_ms=planned_ms,
        estimated_script_duration_ms=estimated_ms,
        script_char_count=char_count,
        beat_coverage=round(coverage, 4),
        evidence_claim_count=sum(
            beat.evidence_need.required for beat in planning.beats
        ),
        issues=issues,
    )
