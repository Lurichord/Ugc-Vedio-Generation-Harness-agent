from __future__ import annotations

from ...harness.models import ArollCharacter
from ..narrative_agent.models import CreativeBrief, ScriptArtifact
from .models import (
    VoiceGlobalSettings,
    VoicePlan,
    VoiceSegmentPlan,
    VoiceSpeaker,
)


_ROLE_SPEED = {
    "question": 1.08,
    "expected_answer": 1.08,
    "reveal": 1.04,
    "claim": 1.02,
    "evidence": 0.96,
    "explanation": 0.98,
    "example": 1.03,
    "contrast": 1.08,
    "deepening": 0.95,
    "implication": 0.98,
    "payoff": 0.93,
    "callback": 0.98,
}

_ROLE_TONE = {
    "question": "好奇、直接，句尾形成明确提问",
    "expected_answer": "像在复述观众的第一反应",
    "reveal": "带反转感，关键词前稍作蓄力",
    "claim": "笃定但不过度播音腔",
    "evidence": "可信、清楚，给观众处理信息的空间",
    "explanation": "耐心解释，保持口语感",
    "example": "具体、亲近，像分享真实例子",
    "contrast": "前后反差鲜明，转折词有力度",
    "deepening": "稍微收慢，体现思考深入",
    "implication": "从事实转向影响，语气有推进",
    "payoff": "收束、有结论感，重点清晰",
    "callback": "自然回扣开头，形成闭环",
}

_TONE_SPEED_DELTA = {
    "information_dense": 0.04,
    "energetic": 0.08,
    "urgent": 0.10,
    "fast": 0.08,
    "calm": -0.08,
    "reflective": -0.06,
    "slow": -0.08,
}


def build_voice_plan(
    brief: CreativeBrief,
    script: ScriptArtifact,
    *,
    voice_id: str,
    provider: str = "volcengine",
    sample_rate: int = 24_000,
    character: ArollCharacter | None = None,
) -> VoicePlan:
    tone_delta = sum(
        _TONE_SPEED_DELTA.get(tone.lower(), 0.0)
        for tone in brief.communication.tone
    )
    segments: list[VoiceSegmentPlan] = []
    for index, segment in enumerate(script.segments, start=1):
        hint = segment.delivery_hint
        speed = max(0.8, min(1.25, _ROLE_SPEED[hint.speech_act] + tone_delta))
        segments.append(
            VoiceSegmentPlan(
                voice_segment_id=f"vs{index:02d}",
                script_segment_id=segment.script_segment_id,
                planned_beat_id=segment.planned_beat_id,
                speech_act=hint.speech_act,
                tone=_ROLE_TONE[hint.speech_act],
                speed_ratio=round(speed, 2),
                energy=hint.energy,
                pause_before_ms=hint.pause_before_ms,
                pause_after_ms=hint.pause_after_ms,
                emphasis_words=hint.emphasis_words,
                delivery_instruction=(
                    f"{_ROLE_TONE[hint.speech_act]}；"
                    f"能量为{hint.energy}；语速倍率{speed:.2f}"
                ),
            )
        )
    return VoicePlan(
        project_id=brief.project_id,
        speaker=VoiceSpeaker(
            provider=provider,
            voice_id=voice_id,
            language=brief.target.language,
            persona=(
                f"{character.visual_description}；声音："
                f"{character.voice_profile.tone}"
                if character
                else brief.communication.creator_persona
            ),
            character_id=character.character_id if character else None,
            gender=character.voice_profile.gender if character else None,
            age_style=character.voice_profile.age_style if character else None,
        ),
        global_settings=VoiceGlobalSettings(sample_rate=sample_rate),
        source_tones=brief.communication.tone,
        segments=segments,
    )
