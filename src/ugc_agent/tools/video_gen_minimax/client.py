from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from ..ark import env_value, join_url, request_json, require_env
from ..video_gen.client import VIDEO_SUFFIXES, _duration_seconds, write_video

__all__ = ["generate_video", "write_video"]

_SIZES = {
    ("768P", "21:9"): "1536x672",
    ("768P", "16:9"): "1344x768",
    ("768P", "4:3"): "1024x768",
    ("768P", "1:1"): "768x768",
    ("768P", "3:4"): "768x1024",
    ("768P", "9:16"): "768x1344",
    ("2K", "21:9"): "2912x1280",
    ("2K", "16:9"): "2544x1456",
    ("2K", "4:3"): "1920x1440",
    ("2K", "1:1"): "1440x1440",
    ("2K", "3:4"): "1440x1920",
    ("2K", "9:16"): "1440x2560",
}


def _base_url() -> str:
    return env_value("MINIMAX_BASE_URL") or "https://api.minimax.cn"


def _api_key() -> str:
    return require_env("MINIMAX_API_KEY", "UGC_VIDEO_API_KEY")


def _tier(resolution: str | None) -> str:
    raw = (resolution or env_value("UGC_VIDEO_RESOLUTION") or "768P").strip()
    if raw.lower() in {"720p", "768p"}:
        return "768P"
    if raw.lower() in {"2k", "1080p", "4k"}:
        return "2K"
    return "768P"


def _size(aspect_ratio: str, resolution: str | None) -> str:
    return _SIZES.get((_tier(resolution), aspect_ratio), _SIZES[("768P", "9:16")])


def _is_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


def _is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def _form_parts(
    prompt: str,
    *,
    duration_seconds: float,
    mode: str,
    reference_paths: list[str] | None,
    audio_paths: list[str] | None,
    aspect_ratio: str,
    resolution: str | None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str, bytes]]]:
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
    elif mode != "ref2va":
        raise ValueError("mode must be fl2va or ref2va")
    fields: list[tuple[str, str]] = [
        ("model", env_value("MINIMAX_VIDEO_MODEL") or "/models/MiniMax-H3"),
        ("prompt", prompt),
        ("seconds", str(_duration_seconds(duration_seconds))),
        ("size", _size(aspect_ratio, resolution)),
    ]
    files: list[tuple[str, str, str, bytes]] = []
    for path in list(reference_paths or []) + list(audio_paths or []):
        if _is_url(path):
            fields.append(("reference_url", path))
            continue
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(path)
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        files.append(("input_reference", file_path.name, mime, file_path.read_bytes()))
    return fields, files


def _encode_multipart(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, filename, content_type, data in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_form(
    url: str,
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
    *,
    timeout: int = 60,
) -> dict:
    body, content_type = _encode_multipart(fields, files)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"minimax http {exc.code}: {detail}") from exc
    result = json.loads(raw) if raw else {}
    if not isinstance(result, dict):
        raise RuntimeError(f"video-gen-minimax did not return json: {result}")
    return result


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
    fields, files = _form_parts(
        prompt,
        duration_seconds=duration_seconds,
        mode=mode,
        reference_paths=reference_paths,
        audio_paths=audio_paths,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    result = post_form(join_url(_base_url(), "v1/videos"), fields, files, timeout=60)
    task_id = result.get("id") or result.get("task_id") or (result.get("task") or {}).get("id")
    if not task_id:
        raise RuntimeError(f"video-gen-minimax did not return a task id: {result}")
    return str(task_id)


def wait_for_video(
    task_id: str,
    *,
    sleep=time.sleep,
    interval: int = 10,
    timeout: int = 1800,
) -> None:
    url = join_url(_base_url(), "v1/videos", task_id)
    deadline = time.monotonic() + timeout
    while True:
        result = request_json(url, api_key=_api_key(), method="GET", timeout=60)
        task = result.get("task") or result
        status = result.get("status") or task.get("status")
        if status in {"succeeded", "completed"}:
            return
        if status in {"failed", "cancelled", "expired"}:
            error = task.get("error") or result.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error
            raise RuntimeError(error or f"video-gen-minimax {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"video-gen-minimax timed out: {task_id}")
        sleep(interval)


def download_content(task_id: str, *, timeout: int = 180) -> bytes:
    url = join_url(_base_url(), "v1/videos", task_id, "content")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {_api_key()}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"minimax http {exc.code}: {detail}") from exc
    if not data:
        raise RuntimeError("video-gen-minimax returned empty video content")
    return data


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
    wait_for_video(task_id, sleep=sleep)
    return download_content(task_id)
