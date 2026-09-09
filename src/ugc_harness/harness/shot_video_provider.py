from __future__ import annotations

import base64
from collections.abc import Sequence
from pathlib import Path

import httpx

from ..content import DramaVisualSpec, ProductionShot, TutorialVisualSpec
from ..shared.settings import AssetGenerationSettings
from ..shared.video_generation import generate_seedance_video
from .shot_asset_controller import GeneratedShotVideo, ShotReferenceImage


class SeedanceShotVideoProvider:
    """Direct ProductionShot -> AI video capability used by the Harness."""

    def __init__(self, settings: AssetGenerationSettings) -> None:
        self.settings = settings
        self.client = httpx.Client()

    def __enter__(self) -> "SeedanceShotVideoProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def generate(
        self,
        shot: ProductionShot,
        *,
        progress_path: Path,
        reference_images: tuple[ShotReferenceImage, ...] | list[ShotReferenceImage] = (),
    ) -> GeneratedShotVideo:
        labeled = tuple(reference_images)
        prompt = _shot_prompt(shot, labeled)
        target_seconds = (shot.timing.target_duration_ms or 5000) / 1000
        duration = min((4, 6, 8), key=lambda value: abs(value - target_seconds))
        frames = _frame_images(labeled)
        generated = generate_seedance_video(
            self.client,
            self.settings,
            {
                "model": self.settings.video_model,
                "prompt": prompt,
                "resolution": self.settings.video_resolution,
                "aspect_ratio": "9:16",
                "duration": duration,
                "frame_images": frames,
            },
            progress_path=progress_path,
            allow_text_fallback=not frames,
        )
        return GeneratedShotVideo(
            content=generated.content,
            mime_type=generated.mime_type,
            model=self.settings.video_model,
            prompt=prompt,
            job_id=generated.job_id,
            duration_ms=duration * 1000,
            cost_usd=(generated.job.get("usage") or {}).get("cost"),
            last_frame=generated.last_frame,
        )


def _frame_images(references: Sequence[ShotReferenceImage]) -> list[dict[str, object]]:
    return [_image_item(item.path, "reference_image") for item in references]


def _reference_prompt(references: Sequence[ShotReferenceImage]) -> str:
    if not references:
        return ""
    lines = [
        "参考图全部按参考图使用，不要当成首尾帧模式。",
    ]
    for index, item in enumerate(references, start=1):
        lines.append(f"@image{index} 是{item.description}")
    return "\n".join(lines)


def _image_item(path: Path, role: str) -> dict[str, object]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}"},
        "role": role,
        "frame_type": role,
    }


def _shot_prompt(shot: ProductionShot, references: Sequence[ShotReferenceImage]) -> str:
    labels = _reference_prompt(references)
    visual = shot.visual
    if isinstance(visual, DramaVisualSpec):
        dialogue = "；".join(shot.audio.dialogue_lines)
        ambient = shot.audio.ambient_audio or "自然环境声"
        body = (
            f"{visual.generation_prompt}\n动作：{visual.action_description}\n"
            f"镜头：{visual.camera_instruction}\n台词：{dialogue or '无台词'}\n"
            f"声音：生成并保留人物对白与{ambient}，音画同步。\n"
            f"连续性约束：{'；'.join(visual.continuity_constraints) or '保持角色与场景一致'}"
        )
    elif isinstance(visual, TutorialVisualSpec):
        source_types = "、".join(shot.audio.source_audio_types) or "真实制作操作声"
        body = (
            f"竖屏制作教程实拍风格视频。主体：{visual.subject_ref}。"
            f"动作步骤：{shot.purpose}。机位：{visual.camera_angle}。"
            f"关键细节：{visual.critical_detail}。"
            f"{'双手清楚入镜。' if visual.hands_required else ''}"
            f"保留{source_types}，不要生成旁白、字幕、贴纸或背景音乐。"
            "动作必须真实、连续、可跟做，主体和工具在相邻镜头中保持一致。"
        )
    else:
        raise ValueError(f"unsupported direct AI video visual: {visual.realization_type}")
    if labels:
        return f"{body}\n{labels}"
    return body
