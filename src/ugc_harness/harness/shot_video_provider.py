from __future__ import annotations

from pathlib import Path

import httpx

from ..content import DramaVisualSpec, ProductionShot, TutorialVisualSpec
from ..shared.settings import AssetGenerationSettings
from ..shared.video_generation import generate_seedance_video
from .shot_asset_controller import GeneratedShotVideo


class SeedanceShotVideoProvider:
    """Direct ProductionShot -> AI video capability used by the Harness."""

    def __init__(self, settings: AssetGenerationSettings) -> None:
        self.settings = settings
        self.client = httpx.Client()

    def __enter__(self) -> "SeedanceShotVideoProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def generate(self, shot: ProductionShot, *, progress_path: Path) -> GeneratedShotVideo:
        prompt = _shot_prompt(shot)
        target_seconds = (shot.timing.target_duration_ms or 5000) / 1000
        duration = min((4, 6, 8), key=lambda value: abs(value - target_seconds))
        generated = generate_seedance_video(
            self.client,
            self.settings,
            {
                "model": self.settings.video_model,
                "prompt": prompt,
                "resolution": self.settings.video_resolution,
                "aspect_ratio": "9:16",
                "duration": duration,
            },
            progress_path=progress_path,
        )
        return GeneratedShotVideo(
            content=generated.content,
            mime_type=generated.mime_type,
            model=self.settings.video_model,
            prompt=prompt,
            job_id=generated.job_id,
            duration_ms=duration * 1000,
            cost_usd=(generated.job.get("usage") or {}).get("cost"),
        )


def _shot_prompt(shot: ProductionShot) -> str:
    visual = shot.visual
    if isinstance(visual, DramaVisualSpec):
        dialogue = "；".join(shot.audio.dialogue_lines)
        ambient = shot.audio.ambient_audio or "自然环境声"
        return (
            f"{visual.generation_prompt}\n动作：{visual.action_description}\n"
            f"镜头：{visual.camera_instruction}\n台词：{dialogue or '无台词'}\n"
            f"声音：生成并保留人物对白与{ambient}，音画同步。\n"
            f"连续性约束：{'；'.join(visual.continuity_constraints) or '保持角色与场景一致'}"
        )
    if isinstance(visual, TutorialVisualSpec):
        source_types = "、".join(shot.audio.source_audio_types) or "真实制作操作声"
        return (
            f"竖屏制作教程实拍风格视频。主体：{visual.subject_ref}。"
            f"动作步骤：{shot.purpose}。机位：{visual.camera_angle}。"
            f"关键细节：{visual.critical_detail}。"
            f"{'双手清楚入镜。' if visual.hands_required else ''}"
            f"保留{source_types}，不要生成旁白、字幕、贴纸或背景音乐。"
            "动作必须真实、连续、可跟做，主体和工具在相邻镜头中保持一致。"
        )
    raise ValueError(f"unsupported direct AI video visual: {visual.realization_type}")
