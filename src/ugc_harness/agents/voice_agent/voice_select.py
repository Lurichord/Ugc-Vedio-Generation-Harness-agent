from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from ...content import ArollCharacter
from ...shared.models import StrictModel
from ..narrative_agent.models import CreativeBrief


CATALOG_PATH = Path(__file__).with_name("voices.json")

Gender = Literal["male", "female", "neutral"]
AgeStyle = Literal["young", "mature", "senior"]


class VoiceCatalogEntry(StrictModel):
    name: str
    voice_id: str
    gender: Gender
    age_style: AgeStyle
    language: str
    scene: str = "通用"
    tags: list[str] = Field(default_factory=list)
    description: str = ""


class VoiceChoice(StrictModel):
    voice_id: str
    reason: str


class VoicePicker(Protocol):
    def pick(
        self,
        *,
        brief: CreativeBrief,
        character: ArollCharacter | None,
        candidates: list[VoiceCatalogEntry],
        instruction: str | None = None,
    ) -> str: ...


def load_catalog(path: Path | None = None) -> list[VoiceCatalogEntry]:
    payload = json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))
    return [VoiceCatalogEntry.model_validate(item) for item in payload["voices"]]


def language_matches(voice_language: str, brief_language: str) -> bool:
    voice = voice_language.lower().replace("_", "-")
    brief = brief_language.lower().replace("_", "-")
    return voice == brief or voice.split("-", 1)[0] == brief.split("-", 1)[0]


def filter_catalog(
    catalog: list[VoiceCatalogEntry],
    *,
    language: str,
    gender: str | None = None,
    age_style: str | None = None,
) -> list[VoiceCatalogEntry]:
    matched = [item for item in catalog if language_matches(item.language, language)]
    if gender in {"male", "female"}:
        gendered = [item for item in matched if item.gender == gender]
        if gendered:
            matched = gendered
    if age_style in {"young", "mature", "senior"}:
        aged = [item for item in matched if item.age_style == age_style]
        if aged:
            matched = aged
    return matched


def select_voice(
    brief: CreativeBrief,
    character: ArollCharacter | None,
    *,
    catalog: list[VoiceCatalogEntry] | None = None,
    picker: VoicePicker | None = None,
    override_voice_id: str | None = None,
    instruction: str | None = None,
) -> str:
    if override_voice_id:
        return override_voice_id
    voices = catalog if catalog is not None else load_catalog()
    note = (instruction or "").strip()
    profile = None if character is None else character.voice_profile
    # First run may use the narrative voice_profile as a shortlist hint.
    # Repair follows instruction: do not keep filtering by the original gender.
    shortlist = filter_catalog(
        voices,
        language=brief.target.language,
        gender=None if note or profile is None else profile.gender,
        age_style=None if note or profile is None else profile.age_style,
    )
    if not shortlist:
        raise ValueError("音色表里没有匹配当前语言/角色的讲解音色")
    active_picker = picker or LlmVoicePicker()
    return active_picker.pick(
        brief=brief,
        character=character,
        candidates=shortlist,
        instruction=note or None,
    )


class LlmVoicePicker:
    def __init__(self, generator: object | None = None) -> None:
        self._generator = generator

    def pick(
        self,
        *,
        brief: CreativeBrief,
        character: ArollCharacter | None,
        candidates: list[VoiceCatalogEntry],
        instruction: str | None = None,
    ) -> str:
        generator = self._generator
        if generator is None:
            from ...shared.llm import StructuredLLM
            from ...shared.settings import LLMSettings

            generator = StructuredLLM(LLMSettings.from_environment(None, None))
            self._generator = generator
        allowed = {item.voice_id for item in candidates}
        choice = generator.generate(
            _selection_prompt(
                brief, character, candidates, instruction=instruction
            ),
            VoiceChoice,
        )
        if choice.voice_id not in allowed:
            raise ValueError(
                f"模型选出的音色不在短名单中: {choice.voice_id}"
            )
        return choice.voice_id


def _selection_prompt(
    brief: CreativeBrief,
    character: ArollCharacter | None,
    candidates: list[VoiceCatalogEntry],
    instruction: str | None = None,
) -> str:
    profile = None if character is None else character.voice_profile
    speaker = (
        {
            "character_id": character.character_id,
            "visual_description": character.visual_description,
            "gender": profile.gender,
            "age_style": profile.age_style,
            "tone": profile.tone,
            "pace": profile.pace,
        }
        if character is not None and profile is not None
        else None
    )
    shortlist = [
        {
            "name": item.name,
            "voice_id": item.voice_id,
            "gender": item.gender,
            "age_style": item.age_style,
            "scene": item.scene,
            "tags": item.tags,
            "description": item.description,
        }
        for item in candidates
    ]
    lead = (
        "从短名单里选一个豆包语音 2.0 音色，给这条讲解片配旁白。"
        "只服务中文/英文讲解 TTS，不要选角色扮演或卡通音色。"
        "必须返回短名单里已有的 voice_id。"
    )
    if instruction:
        lead += (
            "这是一次局部修复：按用户 instruction 选音色，"
            "不要沿用第一次的性别/年龄档案。\n"
            f"用户这一次要改成：{instruction}\n"
        )
    return (
        f"{lead}\n"
        f"主题：{brief.topic}\n"
        f"目标：{brief.communication.goal}\n"
        f"创作者人设：{brief.communication.creator_persona}\n"
        f"片子口吻：{json.dumps(brief.communication.tone, ensure_ascii=False)}\n"
        f"语言：{brief.target.language}\n"
        f"出镜角色声音：{json.dumps(speaker, ensure_ascii=False)}\n\n"
        "短名单：\n"
        f"{json.dumps(shortlist, ensure_ascii=False)}"
    )
