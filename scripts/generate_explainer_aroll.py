"""Lock one host still, replace a bad B-roll still, and generate A-roll talking heads.

Usage:
    .\\.venv\\Scripts\\python.exe scripts/generate_explainer_aroll.py --project <project_dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ugc_harness.agents.asset_agent.providers import (
    _audio_item,
    _clamp_reference_audio,
    _image_item,
    _image_suffix,
)
from ugc_harness.shared.image_generation import generate_seedream_image
from ugc_harness.shared.media_duration import probe_duration_ms
from ugc_harness.shared.settings import AssetGenerationSettings
from ugc_harness.shared.video_generation import generate_seedance_video

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = ROOT / "outputs" / "不同地区为何有不同的中秋习俗_6a53c5bd"
IDENTITY_REL = "assets/character_reference/host_knowledge.jpg"
HOST_STILL_REL = "assets/generated_image/asset_vr01.jpg"
VR04_PROMPT = (
    "画面无任何字幕。9:16竖屏静帧。传统手工月饼放在木质案板上，"
    "真实室内侧光，食物特写，不要logo不要字幕不要网易标识。"
)
SEEDANCE_DURS = (5, 8, 10, 15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    args = parser.parse_args()
    project = args.project.resolve()
    if not (project / "13_visual_requirements.json").is_file():
        raise SystemExit(f"不是有效项目目录: {project}")

    settings = _settings()
    editorial = _load(project / "editorial_artifact.json")
    beats = {
        item["beat_id"]: item
        for item in _load(project / "10_realized_beats.json")["beats"]
    }
    audio_segments = {
        item["audio_segment_id"]: item
        for item in _load(project / "08_timed_audio.json")["segments"]
    }
    visuals = {
        item["visual_request_id"]: item
        for item in _load(project / "13_visual_requirements.json")["requirements"]
    }
    visual_order = [
        item["visual_request_id"]
        for item in _load(project / "13_visual_requirements.json")["requirements"]
    ]
    profile = editorial["editorial_plan"]["video_profile"]
    character_id = profile["character_id"]
    character_description = profile["character_description"]

    identity = _lock_identity(project)
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        vr04 = _replace_vr04(client, settings, project)
        videos = _generate_talking_heads(
            client,
            settings,
            project,
            visuals=visuals,
            visual_order=visual_order,
            beats=beats,
            audio_segments=audio_segments,
            identity=identity,
            character_id=character_id,
            character_description=character_description,
        )
    _patch_artifacts(project, vr04=vr04, videos=videos)
    print("完成。口播成片：")
    for item in videos:
        print(f"  {item['visual_request_id']} -> {item['local_path']}")
    print(f"  vr04 -> {vr04['local_path']}")


def _settings() -> AssetGenerationSettings:
    settings = AssetGenerationSettings.from_environment(ROOT / ".env")
    updates: dict[str, object] = {"video_poll_seconds": 5}
    if not settings.video_api_key:
        updates["video_api_key"] = settings.image_api_key
    if not settings.video_base_url:
        updates["video_base_url"] = settings.image_base_url
    return settings.model_copy(update=updates)


def _lock_identity(project: Path) -> Path:
    source = project / HOST_STILL_REL
    if not source.is_file():
        raise FileNotFoundError(source)
    dest = project / IDENTITY_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    print(f"人设锁定为 {dest.relative_to(project).as_posix()}")
    return dest


def _replace_vr04(
    client: httpx.Client,
    settings: AssetGenerationSettings,
    project: Path,
) -> dict[str, object]:
    print("正在用 Seedream 替换 vr04 网易 Logo…")
    generated = generate_seedream_image(client, settings, VR04_PROMPT)
    folder = project / "assets" / "generated_image"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"asset_vr04{_image_suffix(generated.mime_type)}"
    output.write_bytes(generated.content)
    old = project / "assets" / "real_image" / "asset_vr04.png"
    if old.is_file():
        old.unlink()
    return {
        "asset_id": "asset_vr04",
        "visual_request_id": "vr04",
        "direction_id": "vr04_d03",
        "beat_id": "b04",
        "modality": "ai_image",
        "origin": "generated",
        "local_path": output.relative_to(project).as_posix(),
        "mime_type": generated.mime_type,
        "sha256": hashlib.sha256(generated.content).hexdigest(),
        "duration_ms": None,
        "source": None,
        "generated_media_disclosure_required": True,
        "generator_model": settings.image_model,
        "generation_prompt": VR04_PROMPT,
        "generation_job_id": None,
        "generation_cost_usd": (generated.response.get("usage") or {}).get("cost"),
        "usability_review": None,
        "production_ready": True,
        "character_id": None,
        "continuity_group_id": None,
        "previous_asset_id": None,
        "identity_reference_path": None,
    }


def _generate_talking_heads(
    client: httpx.Client,
    settings: AssetGenerationSettings,
    project: Path,
    *,
    visuals: dict[str, dict],
    visual_order: list[str],
    beats: dict[str, dict],
    audio_segments: dict[str, dict],
    identity: Path,
    character_id: str,
    character_description: str,
) -> list[dict[str, object]]:
    folder = project / "assets" / "generated_video"
    folder.mkdir(parents=True, exist_ok=True)
    last_dir = project / "assets" / "character_reference"
    last_dir.mkdir(parents=True, exist_ok=True)
    cards: list[dict[str, object]] = []
    previous_visual: str | None = None
    previous_asset_id: str | None = None
    group_index = 0
    for index, visual_id in enumerate(visual_order):
        visual = visuals[visual_id]
        if visual["track"] != "a_roll":
            continue
        beat = beats[visual["beat_id"]]
        prior_id = visual_order[index - 1] if index else None
        prior = visuals.get(prior_id) if prior_id else None
        continue_from = bool(
            prior
            and prior["track"] == "a_roll"
            and visual["track"] == "a_roll"
            and prior["character_id"] == visual["character_id"]
        )
        if not continue_from:
            group_index += 1
            previous_asset_id = None
        group_id = f"{character_id}_group_{group_index:02d}"
        last_frame = (
            last_dir / f"asset_{previous_visual}_last.png"
            if continue_from and previous_visual
            else None
        )
        if last_frame is not None and not last_frame.is_file():
            raise FileNotFoundError(f"接镜缺少尾帧: {last_frame}")
        prompt = _talking_head_prompt(
            narration=beat["narration"],
            character_description=character_description,
            continue_from=continue_from,
        )
        frames = []
        if last_frame is not None:
            frames.append(_image_item(last_frame, "reference_image"))
        frames.append(_image_item(identity, "reference_image"))
        audio_meta = audio_segments[beat["audio_segment_ids"][0]]
        fitted = project / "harness" / "seedance_audio" / f"{visual_id}.wav"
        _clamp_reference_audio(project / audio_meta["file"], fitted)
        duration = _seedance_duration(beat["duration_ms"])
        print(
            f"正在生成 {visual_id} 口播 "
            f"{'接镜' if continue_from else '新开'} dur={duration}s…"
        )
        generated = generate_seedance_video(
            client,
            settings,
            {
                "model": settings.video_model,
                "prompt": prompt,
                "resolution": settings.video_resolution,
                "aspect_ratio": "9:16",
                "duration": duration,
                "generate_audio": True,
                "frame_images": frames,
                "audio_urls": [_audio_item(fitted)],
            },
            progress_path=project / "harness" / "seedance_jobs" / f"{visual_id}.json",
            allow_text_fallback=False,
        )
        output = folder / f"asset_{visual_id}.mp4"
        output.write_bytes(generated.content)
        if generated.last_frame:
            (last_dir / f"asset_{visual_id}_last.png").write_bytes(generated.last_frame)
        asset_id = f"asset_{visual_id}"
        talking = next(
            item
            for item in visual["directions"]
            if item["asset_type"] == "talking_head"
        )
        cards.append(
            {
                "asset_id": asset_id,
                "visual_request_id": visual_id,
                "direction_id": talking["direction_id"],
                "beat_id": beat["beat_id"],
                "modality": "talking_head",
                "origin": "generated",
                "local_path": output.relative_to(project).as_posix(),
                "mime_type": generated.mime_type,
                "sha256": hashlib.sha256(generated.content).hexdigest(),
                "duration_ms": probe_duration_ms(output),
                "source": None,
                "generated_media_disclosure_required": True,
                "generator_model": settings.video_model,
                "generation_prompt": prompt,
                "generation_job_id": generated.job_id,
                "generation_cost_usd": (generated.job.get("usage") or {}).get("cost"),
                "usability_review": None,
                "production_ready": True,
                "character_id": character_id,
                "continuity_group_id": group_id,
                "previous_asset_id": previous_asset_id,
                "identity_reference_path": IDENTITY_REL,
            }
        )
        previous_visual = visual_id
        previous_asset_id = asset_id
    return cards


def _talking_head_prompt(
    *,
    narration: str,
    character_description: str,
    continue_from: bool,
) -> str:
    if continue_from:
        identity_n, last_n = 2, 1
        start = (
            f"图片{last_n}是上一镜尾帧。本镜起幅与图片{last_n}同一姿态、同一机位，不要跳变。"
        )
    else:
        identity_n, start = 1, ""
    lines = [
        "画面无任何字幕。",
        (
            f"将图片{identity_n}中的中分棕发、米白卫衣、浅木书桌、简约中式挂画"
            f"定义为主持人。主持人的面部特征参考图片{identity_n}。"
            f"{character_description}。"
        ),
        f"参考图片{identity_n}中的主持人，生成接下来的画面。",
        "9:16 竖屏中景，真实室内光线，手机直出。",
    ]
    if start:
        lines.append(start)
    lines.extend(
        [
            (
                f"镜头1：机位固定。主持人看着镜头，自然眨眼，开口说{{{narration}}}，"
                "口型与这句话同步，音色和节奏跟@音频1走。"
            ),
            f"面部、发型、衣服始终与图片{identity_n}一致。",
            "不要生成任何文字、字幕、Logo、水印。",
        ]
    )
    return "\n".join(lines)


def _seedance_duration(duration_ms: int) -> int:
    seconds = min(15, max(5, round(duration_ms / 1000)))
    return min(SEEDANCE_DURS, key=lambda item: abs(item - seconds))


def _patch_artifacts(
    project: Path,
    *,
    vr04: dict[str, object],
    videos: list[dict[str, object]],
) -> None:
    by_visual = {item["visual_request_id"]: item for item in [*videos, vr04]}
    for name in ("15_asset_cards.json", "asset_artifact.json"):
        path = project / name
        payload = _load(path)
        payload["assets"] = [
            by_visual.get(item["visual_request_id"], item)
            for item in payload["assets"]
        ]
        if name == "asset_artifact.json":
            payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        _dump(path, payload)

    resolutions = _load(project / "16_visual_resolutions.json")
    for item in resolutions["resolutions"]:
        visual_id = item["visual_request_id"]
        card = by_visual.get(visual_id)
        if card is None:
            continue
        item["selected_direction_id"] = card["direction_id"]
        item["asset_id"] = card["asset_id"]
        item["status"] = "resolved"
        if visual_id == "vr04":
            item["attempts"] = [
                {
                    "direction_id": "vr04_d01",
                    "order": 1,
                    "status": "error",
                    "reason": "检索结果是品牌 Logo，已替换为 AI 静帧",
                },
                {
                    "direction_id": "vr04_d03",
                    "order": 3,
                    "status": "success",
                    "reason": "已生成一份 Seedream 图片素材",
                },
            ]
        else:
            item["attempts"] = [
                {
                    "direction_id": card["direction_id"],
                    "order": 1,
                    "status": "success",
                    "reason": "已生成一份口播视频",
                }
            ]
    _dump(project / "16_visual_resolutions.json", resolutions)
    artifact = _load(project / "asset_artifact.json")
    artifact["resolutions"] = resolutions["resolutions"]
    _dump(project / "asset_artifact.json", artifact)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
