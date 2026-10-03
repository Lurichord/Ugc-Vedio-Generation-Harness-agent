"""Synthesize drama dialogue from each shot's own spoken line.

讲解片的 VoicePlan 是单人旁白，这里不走那条路。短剧台词已经写在镜头提示词里：
谁开口、这句话怎么说，都从「开口说{台词}」那一句读出来，再交给豆包语音 2.0。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ...content import DramaCharacter, ProductionShot
from ...shared.settings import TTSSettings
from ..narrative_agent.models import DramaPlanningArtifact, NarrativeArtifact
from .tts import VolcengineTTS

_SPOKEN = re.compile(r"开口说\{([^{}]+)\}，口型与这句话同步，([^。\n]+)")


@dataclass(frozen=True)
class DialogueLine:
    shot_id: str
    character_id: str
    character_name: str
    voice_name: str
    voice_id: str
    script_line: str
    spoken_text: str
    tone: str
    delivery_instruction: str
    speed_ratio: float
    pitch: int | None
    loudness_rate: int


def synthesize_project(
    project_dir: Path,
    settings: TTSSettings | None = None,
    only_shot_ids: set[str] | None = None,
) -> Path:
    artifact = _load_narrative(project_dir)
    planning = artifact.planning
    if not isinstance(planning, DramaPlanningArtifact):
        raise ValueError("只有短剧项目才按镜头对白生成 TTS")
    if artifact.shots is None:
        raise ValueError("项目还没有镜头")
    lines = plan_lines(planning.characters, artifact.shots.shots)
    if only_shot_ids:
        lines = [item for item in lines if item.shot_id in only_shot_ids]
    if not lines:
        raise ValueError("镜头里没有对白")
    active = settings or TTSSettings.from_environment()
    out_dir = project_dir / "audio" / "dialogue"
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for line in lines:
        wav_path = out_dir / f"{line.shot_id}.wav"
        print(
            f"tts {line.shot_id} {line.character_name} {line.voice_name} "
            f"pitch={line.pitch}",
            flush=True,
        )
        speaker = active.model_copy(update={"voice_id": line.voice_id})
        with VolcengineTTS(speaker) as tts:
            result = tts.synthesize(
                text=line.spoken_text,
                speed_ratio=line.speed_ratio,
                output_path=wav_path,
                delivery_instruction=line.delivery_instruction,
                pitch=line.pitch,
                loudness_rate=line.loudness_rate,
            )
        records.append(
            {
                "shot_id": line.shot_id,
                "character_id": line.character_id,
                "character_name": line.character_name,
                "voice_name": line.voice_name,
                "voice_id": line.voice_id,
                "script_line": line.script_line,
                "spoken_text": line.spoken_text,
                "tone": line.tone,
                "delivery_instruction": line.delivery_instruction,
                "speed_ratio": line.speed_ratio,
                "pitch": line.pitch,
                "loudness_rate": line.loudness_rate,
                "file": wav_path.relative_to(project_dir).as_posix(),
                "duration_ms": result.duration_ms,
                "request_id": result.request_id,
            }
        )
        print(f"  {wav_path.name} {result.duration_ms}ms", flush=True)
    manifest = out_dir / "dialogue_manifest.json"
    if only_shot_ids and manifest.is_file():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        kept = [
            item
            for item in previous.get("lines", [])
            if item.get("shot_id") not in only_shot_ids
        ]
        records = kept + records
        records.sort(key=lambda item: str(item.get("shot_id")))
    manifest.write_text(
        json.dumps({"lines": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _load_narrative(project_dir: Path) -> NarrativeArtifact:
    payload = json.loads(
        (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
    )
    shots = (payload.get("shots") or {}).get("shots") or []
    for shot in shots:
        if isinstance(shot, dict):
            shot.pop("generation_references", None)
    return NarrativeArtifact.model_validate(payload)


def plan_lines(
    characters: list[DramaCharacter],
    shots: list[ProductionShot],
) -> list[DialogueLine]:
    by_id = {item.character_id: item for item in characters}
    planned: list[DialogueLine] = []
    for shot in shots:
        if shot.audio.audio_mode != "embedded_in_video":
            continue
        if not shot.audio.dialogue_lines:
            continue
        if shot.visual.realization_type != "generated_scene":
            continue
        sentence = _spoken_sentence(shot.visual.generation_prompt)
        found = _SPOKEN.search(sentence)
        if found is None:
            raise ValueError(f"{shot.shot_id} 提示词里没有对白")
        script_line, tone = found.groups()
        script_line = script_line.strip()
        tone = tone.strip("，, ")
        if script_line not in shot.audio.dialogue_lines:
            raise ValueError(f"{shot.shot_id} 提示词台词和镜头台词不一致")
        speaker = _speaker(sentence, shot.visual.character_ids, by_id)
        voice_name, voice_id = _voice_for(speaker)
        speed, pitch, loudness, instruction = _delivery(tone, script_line)
        planned.append(
            DialogueLine(
                shot_id=shot.shot_id,
                character_id=speaker.character_id,
                character_name=speaker.name,
                voice_name=voice_name,
                voice_id=voice_id,
                script_line=script_line,
                spoken_text=_spoken_text(script_line, tone),
                tone=tone,
                delivery_instruction=instruction,
                speed_ratio=speed,
                pitch=pitch,
                loudness_rate=loudness,
            )
        )
    return planned


def _spoken_sentence(prompt: str) -> str:
    for part in prompt.splitlines():
        if _SPOKEN.search(part):
            return part.strip()
    raise ValueError("镜头提示词里没有「开口说{台词}」")


def _speaker(
    sentence: str,
    present_ids: list[str],
    characters: dict[str, DramaCharacter],
) -> DramaCharacter:
    present = [characters[item] for item in present_ids if item in characters]
    head = sentence.split("开口说", 1)[0]
    named = [item for item in present if item.name and item.name in head]
    if len(named) == 1:
        return named[0]
    if "她" in head:
        females = [item for item in present if "女" in "".join(item.voice_constraints)]
        if len(females) == 1:
            return females[0]
    if "他" in head:
        males = [item for item in present if "男" in "".join(item.voice_constraints)]
        if len(males) == 1:
            return males[0]
    raise ValueError(f"无法判断这句是谁说的: {sentence}")


def _voice_for(character: DramaCharacter) -> tuple[str, str]:
    text = "".join(character.voice_constraints)
    female = "女" in text
    if female and "青年" in text:
        return "Vivi 2.0", "zh_female_vv_uranus_bigtts"
    if female and "中年" in text:
        return "邻居阿姨 2.0", "ICL_uranus_zh_female_linjuayi_tob"
    if (not female) and "老年" in text:
        return "云舟 2.0", "zh_male_m191_uranus_bigtts"
    if (not female) and "中年" in text:
        return "刘飞 2.0", "zh_male_liufei_uranus_bigtts"
    raise ValueError(
        f"{character.name} 的声音条件没有对应音色: {character.voice_constraints}"
    )


def _delivery(tone: str, line: str) -> tuple[float, int | None, int, str]:
    """语气只写一句日常说法。不改音高，避免叠出不像人的声线。"""
    speed, loudness = 1.0, 0
    if "侥幸" in tone:
        speed, loudness = 0.98, -8
        note = "轻声说，像旁边有人，但仍然是正常说话"
    elif "爽朗" in tone or "大嗓门" in tone:
        speed, loudness = 1.04, 12
        note = "在家里爽快地说，音量正常偏亮，不要喊"
    elif "慌张" in tone or "吞吞吐吐" in tone:
        speed, loudness = 1.06, 8
        note = "有点急，话说到一半停住，不要尖叫"
    elif "低音" in tone or "憨厚" in tone:
        speed, loudness = 0.98, 4
        note = "慢一点，随和地解释，不要压着嗓子"
    elif "随和" in tone:
        note = "像串门聊天，放松地说"
        if "没发现" in tone:
            note = "轻松地劝人尝尝，不要故作惊讶"
    else:
        note = "用日常说话的口气"
    if line.endswith("…"):
        note += "，最后一个词不要说完"
    return speed, None, loudness, f"用口语说。{note}。不要播音腔，不要夸张。"


def _spoken_text(line: str, tone: str) -> str:
    del tone
    text = line.strip()
    if not text.endswith(("。", "！", "？", "…")):
        text += "。"
    return text


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("用法: python -m ugc_harness.agents.voice_agent.dialogue <项目目录>")
    manifest = synthesize_project(Path(sys.argv[1]))
    print(f"wrote {manifest}", flush=True)


if __name__ == "__main__":
    main()
