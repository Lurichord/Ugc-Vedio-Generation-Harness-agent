from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import httpx

from ...shared.settings import AssetGenerationSettings, LLMSettings
from ...shared.video_generation import generate_seedance_video
from ..asset_agent.models import AssetCard
from ..editorial_agent.models import ExplorationDirection
from ..voice_agent.models import RealizedBeat
from .models import DerivedAsset


class VolcengineScreenAnimationProvider:
    """Turn a conceptual UI image into an AI-generated screen interaction video."""

    def __init__(
        self,
        llm_settings: LLMSettings,
        generation_settings: AssetGenerationSettings,
    ) -> None:
        self.llm_settings = llm_settings
        self.generation_settings = generation_settings
        self.client = httpx.Client(
            timeout=llm_settings.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "ugc-video-harness/0.4.2"},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "VolcengineScreenAnimationProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def generate(
        self,
        *,
        asset: AssetCard,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
    ) -> DerivedAsset:
        source = project_dir / asset.local_path
        if not source.is_file():
            raise FileNotFoundError(f"screen source image does not exist: {source}")
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        prompt = _screen_animation_prompt(beat, direction)
        generated = generate_seedance_video(
            self.client,
            self.generation_settings,
            {
                "model": self.generation_settings.video_model,
                "prompt": prompt,
                "aspect_ratio": "9:16",
                "duration": min(
                    (4, 6, 8),
                    key=lambda item: abs(item - beat.duration_ms / 1000),
                ),
                "frame_images": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{asset.mime_type};base64,{encoded}"
                        },
                        "frame_type": "first_frame",
                    }
                ],
            },
            progress_path=(
                project_dir
                / "harness"
                / "seedance_jobs"
                / f"timeline_{asset.visual_request_id}.json"
            ),
        )
        raw = generated.content
        derivative_id = f"derived_{asset.visual_request_id}_screen_video"
        folder = project_dir / "assets" / "timeline_generated_video"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{derivative_id}.mp4"
        output.write_bytes(raw)
        return DerivedAsset(
            derivative_id=derivative_id,
            source_asset_id=asset.asset_id,
            beat_id=beat.beat_id,
            derivation_type="ai_image_to_video",
            local_path=output.relative_to(project_dir).as_posix(),
            mime_type=generated.mime_type,
            sha256=hashlib.sha256(raw).hexdigest(),
            generator_model=self.generation_settings.video_model,
            generation_prompt=prompt,
            generation_job_id=generated.job_id,
            generation_cost_usd=(generated.job.get("usage") or {}).get("cost"),
        )

        # Legacy OpenAI-style video protocol kept below temporarily for old projects.
        video_api_key, video_base_url = _video_api_config(
            self.generation_settings
        )
        source = project_dir / asset.local_path
        if not source.is_file():
            raise FileNotFoundError(f"screen source image does not exist: {source}")
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        prompt = _screen_animation_prompt(beat, direction)
        response = self.client.post(
            video_base_url + "/videos",
            headers=_auth_headers(video_api_key),
            json={
                "model": self.generation_settings.video_model,
                "prompt": prompt,
                "resolution": self.generation_settings.video_resolution,
                "aspect_ratio": "9:16",
                "generate_audio": False,
                "frame_images": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{asset.mime_type};base64,{encoded}"
                            )
                        },
                        "frame_type": "first_frame",
                    }
                ],
            },
        )
        response.raise_for_status()
        job = response.json()
        job_id = str(job["id"])
        polling_url = str(
            job.get("polling_url")
            or video_base_url + f"/videos/{job_id}"
        )
        deadline = time.monotonic() + self.generation_settings.video_timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"screen animation job {job_id} timed out")
            time.sleep(self.generation_settings.video_poll_seconds)
            status_response = self.client.get(
                polling_url,
                headers=_auth_headers(video_api_key),
            )
            status_response.raise_for_status()
            job = status_response.json()
            status = job.get("status")
            if status == "completed":
                break
            if status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(
                    f"screen animation job {status}: "
                    f"{job.get('error') or 'unknown error'}"
                )

        urls = job.get("unsigned_urls") or []
        content_url = urls[0] if urls else (
            video_base_url + f"/videos/{job_id}/content?index=0"
        )
        content = self.client.get(
            content_url,
            headers=_auth_headers(video_api_key),
            timeout=120,
        )
        content.raise_for_status()
        raw = content.content
        if not raw or len(raw) > 200 * 1024 * 1024:
            raise RuntimeError("screen animation is empty or exceeds 200MB")
        derivative_id = f"derived_{asset.visual_request_id}_screen_video"
        folder = project_dir / "assets" / "timeline_generated_video"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{derivative_id}.mp4"
        output.write_bytes(raw)
        return DerivedAsset(
            derivative_id=derivative_id,
            source_asset_id=asset.asset_id,
            beat_id=beat.beat_id,
            derivation_type="ai_image_to_video",
            local_path=output.relative_to(project_dir).as_posix(),
            mime_type=content.headers.get("content-type", "video/mp4").split(";")[0],
            sha256=hashlib.sha256(raw).hexdigest(),
            generator_model=self.generation_settings.video_model,
            generation_prompt=prompt,
            generation_job_id=job_id,
            generation_cost_usd=(job.get("usage") or {}).get("cost"),
        )


def _screen_animation_prompt(
    beat: RealizedBeat,
    direction: ExplorationDirection,
) -> str:
    return f"""Animate the supplied conceptual UI image into a natural vertical
screen-recording-style UGC insert. Preserve the existing layout and wording as
closely as possible. Add a visible cursor, one purposeful click, a short smooth
scroll, and a subtle interface response. Do not redesign the page, introduce new
facts, add logos, add fake citations, or turn it into a cinematic scene. No camera
movement outside the screen. No audio.
Narration context: {beat.narration}
Visual purpose: {direction.description}
This is an illustrative conceptual interface, not an authentic product recording."""


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _video_api_config(
    settings: AssetGenerationSettings,
) -> tuple[str, str]:
    if not settings.video_api_key or not settings.video_base_url:
        raise ValueError(
            "Missing Seedance video API configuration. Set "
            "UGC_VIDEO_API_KEY and UGC_VIDEO_BASE_URL."
        )
    return settings.video_api_key, settings.video_base_url.rstrip("/")
