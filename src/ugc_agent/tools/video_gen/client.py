from __future__ import annotations

import time
from pathlib import Path

from ..ark import (
    data_url,
    download_bytes,
    env_value,
    join_url,
    request_json,
    require_env,
    write_unique_file,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".heic", ".heif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


def _duration_seconds(value: float) -> int:
    seconds = int(round(value))
    if seconds < 4:
        return 4
    if seconds > 15:
        return 15
    return seconds


def _media_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return data_url(path)


def _is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def _is_image(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in IMAGE_SUFFIXES or path.startswith(("http://", "https://"))


def _content(
    prompt: str,
    reference_paths: list[str] | None,
    audio_paths: list[str] | None,
    mode: str,
) -> list[dict]:
    items: list[dict] = [{"type": "text", "text": prompt}]
    if mode == "fl2va":
        if audio_paths:
            raise ValueError("fl2va 不能传参考音频，只传一张首帧图")
        paths = list(reference_paths or [])
        if not paths:
            raise ValueError("fl2va 必须传入一张首帧图")
        if len(paths) != 1:
            raise ValueError("fl2va 只能传入一张首帧图，不能带参考视频或其他参考图")
        if _is_video(paths[0]):
            raise ValueError("fl2va 只能传入首帧图片，不能传参考视频")
        if not _is_image(paths[0]):
            raise ValueError(f"unsupported first frame: {paths[0]}")
        items.append(
            {
                "type": "image_url",
                "image_url": {"url": _media_url(paths[0])},
                "role": "first_frame",
            }
        )
        return items
    if mode != "ref2va":
        raise ValueError("mode must be fl2va or ref2va")
    has_visual = False
    for path in reference_paths or []:
        url = _media_url(path)
        if _is_video(path):
            items.append(
                {
                    "type": "video_url",
                    "video_url": {"url": url},
                    "role": "reference_video",
                }
            )
            has_visual = True
            continue
        if not _is_image(path):
            raise ValueError(f"unsupported reference file: {path}")
        items.append(
            {
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            }
        )
        has_visual = True
    for path in audio_paths or []:
        items.append(
            {
                "type": "audio_url",
                "audio_url": {"url": _media_url(path)},
                "role": "reference_audio",
            }
        )
    if audio_paths and not has_visual:
        raise ValueError("ref2va 不能只传音频，至少再传一张参考图或一段参考视频")
    return items


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
    api_key = require_env("UGC_VIDEO_API_KEY", "VOLCENGINE_ARK_API_KEY")
    base_url = require_env("UGC_VIDEO_BASE_URL", "VOLCENGINE_ARK_BASE_URL")
    model = require_env("UGC_VIDEO_MODEL")
    payload = {
        "model": model,
        "content": _content(prompt, reference_paths, audio_paths, mode),
        "duration": _duration_seconds(duration_seconds),
        "ratio": aspect_ratio,
        "resolution": resolution or env_value("UGC_VIDEO_RESOLUTION") or "720p",
        "generate_audio": True,
        "watermark": False,
    }
    result = request_json(
        join_url(base_url, "contents/generations/tasks"),
        api_key=api_key,
        payload=payload,
        timeout=60,
    )
    task_id = result.get("id")
    if not task_id:
        raise RuntimeError(f"video-gen did not return a task id: {result}")
    return str(task_id)


def wait_for_video(
    task_id: str,
    *,
    sleep=time.sleep,
    interval: int = 5,
    timeout: int = 1800,
) -> str:
    api_key = require_env("UGC_VIDEO_API_KEY", "VOLCENGINE_ARK_API_KEY")
    base_url = require_env("UGC_VIDEO_BASE_URL", "VOLCENGINE_ARK_BASE_URL")
    url = join_url(base_url, "contents/generations/tasks", task_id)
    deadline = time.monotonic() + timeout
    while True:
        result = request_json(url, api_key=api_key, method="GET", timeout=60)
        status = result.get("status")
        if status == "succeeded":
            video_url = (result.get("content") or {}).get("video_url")
            if not video_url:
                raise RuntimeError("video-gen succeeded without video_url")
            return str(video_url)
        if status in {"failed", "expired"}:
            error = result.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error
            raise RuntimeError(error or f"video-gen {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"video-gen timed out: {task_id}")
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
    video_url = wait_for_video(task_id, sleep=sleep)
    return download_bytes(video_url)


def write_video(directory: Path, data: bytes) -> Path:
    return write_unique_file(directory, ".mp4", data)
