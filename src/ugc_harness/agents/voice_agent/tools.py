from __future__ import annotations

from pathlib import Path
from typing import Any

from ..generic import RegistryTool, RegistryToolTransport
from ..narrative_agent.models import NarrativeArtifact
from .capabilities import VoiceCapabilities
from .models import VoiceArtifact, VoicePlan
from .planning import build_voice_plan
from .voice_select import VoiceChoice, VoicePicker


SELECT_TOOL = "voice.select_voice"
PLAN_TOOL = "voice.create_plan"
SYNTHESIZE_TOOL = "audio.synthesize_narration"
SUBMIT_TOOL = "voice.submit_candidate"
VOICE_TOOLS = (SELECT_TOOL, PLAN_TOOL, SYNTHESIZE_TOOL, SUBMIT_TOOL)


class VoiceToolSession:
    """In-process Voice Agent tools: select voice, plan, synthesize, submit."""

    def __init__(
        self,
        capabilities: VoiceCapabilities,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        *,
        override_voice_id: str | None = None,
        picker: VoicePicker | None = None,
        instruction: str | None = None,
        current_voice: VoiceArtifact | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.narrative = narrative
        self.project_dir = project_dir
        self.override_voice_id = override_voice_id
        self.picker = picker
        self.instruction = (instruction or "").strip() or None
        self.current_voice = current_voice
        self.selected_voice_id: str | None = None
        self.plan: VoicePlan | None = None
        self.artifact: VoiceArtifact | None = None
        previous = None if current_voice is None else current_voice.voice_plan.speaker.voice_id
        seed = override_voice_id or previous
        if seed:
            self._apply_voice(seed)

    def select_voice(self, problems: list[str] | None = None) -> VoiceChoice:
        del problems
        character = self.narrative.planning.world_state.aroll_character
        choice = self.capabilities.select_narration_voice(
            self.narrative.brief,
            character,
            picker=self.picker,
            override_voice_id=self.override_voice_id,
            instruction=self.instruction,
        )
        self._apply_voice(choice.voice_id)
        return choice

    def create_plan(self, problems: list[str] | None = None) -> VoicePlan:
        del problems
        character = self.narrative.planning.world_state.aroll_character
        chosen = self.override_voice_id or self.selected_voice_id
        if not isinstance(chosen, str) or not chosen:
            raise RuntimeError("voice.select_voice must succeed first")
        script = self.narrative.script
        if script is None:
            raise RuntimeError("voice.create_plan requires a spoken script")
        self._apply_voice(chosen)
        self.plan = build_voice_plan(
            self.narrative.brief,
            script,
            voice_id=chosen,
            character=character,
        )
        return self.plan

    def synthesize(self, problems: list[str] | None = None) -> VoiceArtifact:
        del problems
        if not isinstance(self.plan, VoicePlan):
            raise RuntimeError("voice.create_plan must succeed first")
        self.artifact = self.capabilities.synthesize_narration(
            self.narrative,
            self.plan,
            self.project_dir,
        )
        return self.artifact

    def submit(self, problems: list[str] | None = None) -> VoiceArtifact:
        del problems
        if not isinstance(self.artifact, VoiceArtifact):
            raise RuntimeError(
                "candidate is incomplete: narration audio has not been "
                "synthesized yet"
            )
        return self.artifact

    def transport(self) -> RegistryToolTransport:
        return RegistryToolTransport(
            [
                RegistryTool(
                    name=SELECT_TOOL,
                    description=(
                        "从 2.0 讲解音色表选出旁白音色。"
                        "首跑且 voice_context.voice_id 为空时必须先调用，不要自己编 voice_id。"
                        "局部修复时按 instruction 重选，不要沿用第一次的性别档案。"
                    ),
                    handler=self.select_voice,
                ),
                RegistryTool(
                    name=PLAN_TOOL,
                    description=(
                        "根据已批准的叙事脚本生成配音计划：写发声人、语速、"
                        "停顿与逐段语气。需要先选定音色。"
                    ),
                    handler=self.create_plan,
                ),
                RegistryTool(
                    name=SYNTHESIZE_TOOL,
                    description=(
                        "按配音计划合成全部音频段，拼接完整旁白并产出字级"
                        "时间戳与 RealizedBeat。需要先有配音计划。"
                    ),
                    handler=self.synthesize,
                ),
                RegistryTool(
                    name=SUBMIT_TOOL,
                    description="配音候选完整后提交；缺少音频时会返回可修复错误。",
                    handler=self.submit,
                ),
            ]
        )

    def _apply_voice(self, chosen: str) -> None:
        self.selected_voice_id = chosen
        settings = getattr(self.capabilities.provider, "settings", None)
        if settings is not None and hasattr(settings, "voice_id"):
            settings.voice_id = chosen


def make_voice_transport(
    capabilities: VoiceCapabilities,
    narrative: NarrativeArtifact,
    project_dir: str | Path,
    *,
    override_voice_id: str | None = None,
    picker: VoicePicker | None = None,
    instruction: str | None = None,
    current_voice: VoiceArtifact | None = None,
) -> RegistryToolTransport:
    return VoiceToolSession(
        capabilities,
        narrative,
        project_dir,
        override_voice_id=override_voice_id,
        picker=picker,
        instruction=instruction,
        current_voice=current_voice,
    ).transport()


def voice_task_context(
    narrative: NarrativeArtifact,
    *,
    override_voice_id: str | None = None,
    instruction: str | None = None,
    current_voice: VoiceArtifact | None = None,
) -> dict[str, Any]:
    segments = (
        len(narrative.script.segments) if narrative.script is not None else 0
    )
    character = narrative.planning.world_state.aroll_character
    profile = None if character is None else character.voice_profile
    previous = None if current_voice is None else current_voice.voice_plan.speaker
    payload: dict[str, Any] = {
        "voice_context": {
            "project_id": narrative.brief.project_id,
            "voice_id": override_voice_id or (None if previous is None else previous.voice_id),
            "voice_selection": "override" if override_voice_id else "catalog",
            "speaker_gender": None if profile is None else profile.gender,
            "speaker_age_style": None if profile is None else profile.age_style,
            "script_segment_count": segments,
        }
    }
    note = (instruction or "").strip()
    if note:
        payload["instruction"] = note
    if current_voice is not None:
        payload["current_voice"] = {
            "speaker": current_voice.voice_plan.speaker.model_dump(mode="json"),
            "segments": [
                {
                    "voice_segment_id": item.voice_segment_id,
                    "script_segment_id": item.script_segment_id,
                    "tone": item.tone,
                    "speed_ratio": item.speed_ratio,
                    "energy": item.energy,
                }
                for item in current_voice.voice_plan.segments
            ],
        }
    return payload
