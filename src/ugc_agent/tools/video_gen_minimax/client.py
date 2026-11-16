from __future__ import annotations

import time

from ..ark import download_bytes, env_value, join_url, request_json, require_env
from ..video_gen.client import _content, _duration_seconds, write_video

__all__ = ["generate_video", "write_video"]


def _resolution(value: str | None) -> str:
    raw = (value or env_value("UGC_VIDEO_RESOLUTION") or "768P").strip()
    if raw.lower() == "720p":
        return "768P"
    if raw.lower() == "2k":
        return "2K"
    if raw.lower().endswith("p") and raw[:-1].isdigit():
        return raw[:-1] + "P"
    return raw


def _base_url() -> str:
    return env_value("MINIMAX_BASE_URL") or "https://api.minimax.cn"


def _api_key() -> str:
    return require_env("MINIMAX_API_KEY", "UGC_VIDEO_API_KEY")


def create_video_task(
    prompt: str,
    *,
    duration_seconds: float,
    mode: str,
    reference_paths: list[str] | None = None,
    audio_paths: list[str] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str | None = None,
) -> str:
    payload = {
        "model": env_value("MINIMAX_VIDEO_MODEL") or "/models/MiniMax-H3",
        "content": _content(prompt, reference_paths, audio_paths, mode),
        "duration": _duration_seconds(duration_seconds),
        "resolution": _resolution(resolution),
        "ratio": "adaptive" if mode == "fl2va" else aspect_ratio,
        "aigc_watermark": False,
    }
    result = request_json(
        join_url(_base_url(), "v1/videos"),
        api_key=_api_key(),
        payload=payload,
        timeout=60,
    )
    task_id = result.get("task_id") or result.get("id") or (result.get("task") or {}).get("id")
    if not task_id:
        raise RuntimeError(f"video-gen-minimax did not return a task id: {result}")
    return str(task_id)


def wait_for_video(
    task_id: str,
    *,
    sleep=time.sleep,
    interval: int = 10,
    timeout: int = 1800,
) -> str:
    url = join_url(_base_url(), "v1/videos", task_id)
    deadline = time.monotonic() + timeout
    while True:
        result = request_json(url, api_key=_api_key(), method="GET", timeout=60)
        task = result.get("task") or result
        status = result.get("status") or task.get("status")
        if status in {"succeeded", "completed"}:
            content = task.get("content") or {}
            video_url = content.get("url") or content.get("video_url") or task.get("url")
            if not video_url:
                raise RuntimeError("video-gen-minimax succeeded without video url")
            return str(video_url)
        if status in {"failed", "cancelled", "expired"}:
            error = task.get("error") or result.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error
            raise RuntimeError(error or f"video-gen-minimax {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"video-gen-minimax timed out: {task_id}")
        sleep(interval)


def generate_video(
    prompt: str,
    *,
    duration_seconds: float,
    mode: str,
    reference_paths: list[str] | None = None,
    audio_paths: list[str] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str | None = None,
    sleep=time.sleep,
) -> bytes:
    task_id = create_video_task(
        prompt,
        duration_seconds=duration_seconds,
        mode=mode,
        reference_paths=reference_paths,
        audio_paths=audio_paths,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    return download_bytes(wait_for_video(task_id, sleep=sleep))
