from __future__ import annotations

import base64
from collections.abc import Sequence
from pathlib import Path

import httpx

from ..content import ProductionShot
from ..shared.settings import AssetGenerationSettings
from ..shared.video_generation import generate_seedance_video
from .shot_asset_controller import GeneratedShotVideo, ShotReferenceImage
from .shot_prompt import compose_shot_prompt


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
        prompt: str | None = None,
        replace_existing_job: bool = False,
    ) -> GeneratedShotVideo:
        labeled = tuple(reference_images)
        prompt = _shot_prompt(shot, labeled, prompt)
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
            replace_existing_job=replace_existing_job,
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


def _shot_prompt(
    shot: ProductionShot,
    references: Sequence[ShotReferenceImage],
    prompt: str | None = None,
) -> str:
    text = (prompt or "").strip()
    if text:
        return text
    return compose_shot_prompt(shot, references)
