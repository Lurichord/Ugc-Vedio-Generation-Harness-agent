"""Compose a Seedance fallback prompt from a shot and ordered reference slots.

Asset agent normally writes the prompt. This module only runs when that string
is missing. Narrative already decided the action, camera, and dialogue; here we
number the selected images and follow the generate-shot seven-segment order.
It does not invent who holds what.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..content import (
    DramaVisualSpec,
    EmbeddedSceneAudioSpec,
    ProductionShot,
    TutorialVisualSpec,
)
from .shot_asset_controller import ShotReferenceImage

SlotRole = Literal["continue", "bridge", "cast", "place", "prop", "other"]

_FRAMINGS = (
    "大特写",
    "中近景",
    "近景",
    "中景",
    "全景",
    "远景",
    "特写",
    "俯拍",
    "仰拍",
    "过肩",
)
_TUTORIAL_FRAMING = {
    "top_down": "俯拍",
    "close_up": "特写",
    "medium": "中景",
    "macro": "微距特写",
}
_CAMERA_MOVES = ("缓慢推近", "推近", "推进", "拉远", "摇", "跟拍", "跟随", "环绕", "固定")
_GENERIC_CAMERA = ("保持人物与动作清晰可见", "自然切换")
_ID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PromptSlot:
    index: int
    role: SlotRole
    name: str
    material_id: str


def compose_shot_prompt(
    shot: ProductionShot,
    references: list[ShotReferenceImage] | tuple[ShotReferenceImage, ...],
) -> str:
    slots = prompt_slots(references)
    visual = shot.visual
    if isinstance(visual, DramaVisualSpec):
        return _compose_drama(shot, visual, slots)
    if isinstance(visual, TutorialVisualSpec):
        return _compose_tutorial(visual, slots)
    raise ValueError(f"unsupported direct AI video visual: {visual.realization_type}")


def prompt_slots(
    references: list[ShotReferenceImage] | tuple[ShotReferenceImage, ...],
) -> list[PromptSlot]:
    slots: list[PromptSlot] = []
    for index, item in enumerate(references, start=1):
        role = _slot_role(item)
        slots.append(
            PromptSlot(
                index=index,
                role=role,
                name=_display_name(item, role),
                material_id=item.material_id,
            )
        )
    return slots


def _compose_drama(
    shot: ProductionShot,
    visual: DramaVisualSpec,
    slots: list[PromptSlot],
) -> str:
    action = visual.action_description.strip()
    framing = _framing_from_text(visual.camera_instruction)
    move = _camera_move(visual.camera_instruction)
    dialogue = _dialogue_clause(shot)
    ambient = ""
    audio = shot.audio
    if isinstance(audio, EmbeddedSceneAudioSpec):
        ambient = _sfx_clause(audio.ambient_audio or "")
    return _lines(
        "画面无任何字幕",
        *_definition_lines(slots),
        _task_sentence(slots),
        f"9:16 竖屏{framing}，真实室内光线，手机直出",
        _start_frame(slots),
        _spatial_sentence(slots),
        _beat_sentence(move, action, dialogue),
        ambient,
        _lock_sentence(slots),
        *_closing_constraints(slots),
    )


def _compose_tutorial(
    visual: TutorialVisualSpec,
    slots: list[PromptSlot],
) -> str:
    action = visual.critical_detail.strip()
    if visual.hands_required and "双手" not in action:
        action = f"{action.rstrip('。')}，双手清楚入镜" if action else "双手清楚入镜"
    framing = _TUTORIAL_FRAMING.get(visual.camera_angle, "中景")
    return _lines(
        "画面无任何字幕",
        *_definition_lines(slots),
        _task_sentence(slots),
        f"9:16 竖屏{framing}，真实室内光线，手机直出",
        _start_frame(slots),
        _beat_sentence("机位固定", action, ""),
        "<现场操作声>",
        _lock_sentence(slots),
        *_closing_constraints(slots),
    )


def _definition_lines(slots: list[PromptSlot]) -> list[str]:
    lines: list[str] = []
    for slot in slots:
        if slot.role in {"continue", "bridge"} or not slot.name:
            continue
        lines.append(f"将图片{slot.index}定义为{slot.name}")
        if slot.role == "cast":
            lines.append(f"{slot.name}的面部特征参考图片{slot.index}")
    return lines


def _task_sentence(slots: list[PromptSlot]) -> str:
    subject = _first(slots, "cast") or _first(slots, "prop") or _first(slots, "place")
    if subject is None or not subject.name:
        return "生成接下来的画面"
    return f"参考图片{subject.index}中的{subject.name}，生成接下来的画面"


def _start_frame(slots: list[PromptSlot]) -> str:
    bridge = _first(slots, "bridge")
    if bridge is not None:
        return (
            f"图片{bridge.index}是上一镜尾帧，只用来延续妆造、服装、"
            "材料外观、场景结构和光影，不要沿用上一镜的站位和机位"
        )
    cont = _first(slots, "continue")
    if cont is None:
        return ""
    return (
        f"图片{cont.index}是上一镜尾帧。"
        f"本镜起幅与图片{cont.index}同一姿态、同一机位，不要跳变"
    )


def _spatial_sentence(slots: list[PromptSlot]) -> str:
    casts = [slot for slot in slots if slot.role == "cast" and slot.name]
    place = _first(slots, "place")
    if casts and place is not None and place.name:
        who = "和".join(slot.name for slot in casts)
        return f"{who}站在{place.name}里"
    return ""


def _beat_sentence(move: str, action: str, dialogue: str) -> str:
    motion = action.rstrip("。！？")
    if motion and dialogue:
        body = f"{motion}。{dialogue}"
    else:
        body = dialogue or motion
    camera = move.strip() or "机位固定"
    if body:
        return f"镜头1：{camera}。{body}"
    return f"镜头1：{camera}"


def _dialogue_clause(shot: ProductionShot) -> str:
    audio = shot.audio
    if not isinstance(audio, EmbeddedSceneAudioSpec):
        return ""
    lines = [line.strip() for line in audio.dialogue_lines if line and line.strip()]
    if not lines:
        return ""
    spoken = "，然后说".join(f"{{{line}}}" for line in lines)
    sync = "这句话" if len(lines) == 1 else "这些话"
    return f"开口说{spoken}，口型与{sync}同步"


def _sfx_clause(text: str) -> str:
    tokens = [part.strip() for part in re.split(r"[、，,]", text) if part.strip()]
    if not tokens:
        return ""
    return " ".join(f"<{token}>" for token in tokens)


def _lock_sentence(slots: list[PromptSlot]) -> str:
    parts: list[str] = []
    for slot in slots:
        if slot.role == "continue":
            parts.append(
                f"图片{slot.index}是上一镜尾帧；"
                f"起幅与图片{slot.index}同一姿态、同一机位"
            )
        elif slot.role == "bridge":
            parts.append(
                f"图片{slot.index}是上一镜尾帧，只锁妆造、服装、材料外观、"
                "场景结构和光影，不要沿用站位和机位"
            )
        elif slot.role == "cast":
            parts.append(f"面部、发型、衣服始终与图片{slot.index}一致")
        elif slot.role == "place":
            parts.append(f"房间布局与图片{slot.index}一致")
        elif slot.role == "prop":
            parts.append(f"{slot.name}的形状与图片{slot.index}一致")
    return "；".join(parts)


def _closing_constraints(slots: list[PromptSlot]) -> list[str]:
    lines = ["不要生成任何文字、字幕、Logo、水印"]
    casts = [slot for slot in slots if slot.role == "cast"]
    if len(casts) >= 2:
        lines.append("全程不要出现第二个外形相同的人")
    return lines


def _camera_move(instruction: str) -> str:
    cleaned = instruction.strip()
    for filler in _GENERIC_CAMERA:
        cleaned = cleaned.replace(filler, "")
    cleaned = re.sub(r"[，。；、\s]+", "，", cleaned).strip("，。； ")
    cleaned = cleaned.removeprefix("镜头")
    for verb in _CAMERA_MOVES:
        if verb in cleaned:
            for clause in cleaned.split("，"):
                if verb in clause:
                    return clause.strip() or "机位固定"
    return "机位固定"


def _framing_from_text(instruction: str) -> str:
    for word in _FRAMINGS:
        if word in instruction:
            return word
    return "中景"


def _slot_role(item: ShotReferenceImage) -> SlotRole:
    ident = item.material_id
    if item.kind == "bridge_last_frame":
        return "bridge"
    if item.kind == "previous_last_frame" or ident.startswith("last_frame:"):
        return "continue"
    prefix = ident.partition(":")[0]
    if prefix in {"cast", "place", "prop"}:
        return prefix
    return "other"


def _display_name(item: ShotReferenceImage, role: SlotRole) -> str:
    if role == "continue":
        return "上一镜尾帧"
    if role == "bridge":
        return "上一镜尾帧"
    name = _usable_name(item.name)
    if name:
        return name
    return {"cast": "人物", "place": "场景", "prop": "道具"}.get(role, "参考")


def _usable_name(name: str) -> str:
    text = name.strip()
    if not text or ":" in text or _ID_NAME.fullmatch(text):
        return ""
    return text


def _first(slots: list[PromptSlot], role: SlotRole) -> PromptSlot | None:
    return next((slot for slot in slots if slot.role == role), None)


def _lines(*parts: str) -> str:
    sentences: list[str] = []
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if not text.endswith(("。", "！", "？")):
            text += "。"
        sentences.append(text)
    return "\n".join(sentences)
