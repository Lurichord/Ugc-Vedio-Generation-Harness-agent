from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .settings import AssetGenerationSettings


@dataclass(frozen=True)
class GeneratedVideo:
    job_id: str
    content: bytes
    mime_type: str
    job: dict[str, Any]
    degraded_to_text: bool = False
    last_frame: bytes | None = None


def generate_seedance_video(
    client: httpx.Client,
    settings: AssetGenerationSettings,
    payload: dict[str, object],
    *,
    progress_path: Path | None = None,
    allow_text_fallback: bool = True,
    replace_existing_job: bool = False,
) -> GeneratedVideo:
    """Submit and download a Seedance video through Volcengine Ark's task API.

    When ``allow_text_fallback`` is False, a 400 response for a request that
    carries inline reference images raises instead of silently retrying as
    text-to-video. Character-consistency requests must set it to False, since
    dropping the reference image would let the model reinvent the character.
    """
    api_key, base_url = seedance_api_config(settings)
    degraded_to_text = False
    job = _resumable_job(
        progress_path,
        payload,
        replace_existing_job=replace_existing_job,
    )
    if job is None:
        request_payload = _ark_payload(payload)
        response = client.post(
            f"{base_url}/contents/generations/tasks",
            headers=_auth_headers(api_key),
            json=request_payload,
            timeout=30,
        )
        if response.status_code == 400 and _has_inline_media(request_payload):
            if not allow_text_fallback:
                raise RuntimeError(
                    "Seedance rejected the inline reference image (HTTP 400) "
                    "and this request requires the reference for character "
                    "consistency; refusing to degrade to text-to-video. "
                    f"Ark response: {response.text[:400]}"
                )
            # Ark video generation accepts remote URLs or asset:// references,
            # but not local data URLs. Fall back to text-to-video so a local
            # reference cannot leave the requirement permanently unresolved.
            request_payload["content"] = [
                item
                for item in request_payload["content"]
                if item.get("type") == "text"
            ]
            degraded_to_text = True
            print(
                "Seedance rejected the inline reference; retrying text-to-video",
                flush=True,
            )
            response = client.post(
                f"{base_url}/contents/generations/tasks",
                headers=_auth_headers(api_key),
                json=request_payload,
                timeout=30,
            )
        response.raise_for_status()
        job = response.json()
        job_id = str(job["id"])
        _write_progress(progress_path, payload, job)
        print(f"Seedance task {job_id}: submitted", flush=True)
    else:
        job_id = str(job["id"])
        print(f"Seedance task {job_id}: resuming", flush=True)
    polling_url = f"{base_url}/contents/generations/tasks/{job_id}"
    deadline = time.monotonic() + settings.video_timeout_seconds
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Seedance video task {job_id} timed out")
        if settings.video_poll_seconds > 0:
            time.sleep(settings.video_poll_seconds)
        status_response = client.get(
            polling_url, headers=_auth_headers(api_key), timeout=30
        )
        status_response.raise_for_status()
        job = status_response.json()
        status = str(job.get("status", "")).lower()
        _write_progress(progress_path, payload, job)
        print(f"Seedance task {job_id}: {status or 'unknown'}", flush=True)
        if status == "succeeded":
            break
        if status in {"failed", "cancelled", "canceled", "expired"}:
            error = job.get("error") or job.get("message") or "unknown error"
            raise RuntimeError(f"Seedance video task {status}: {error}")

    content_url = str((job.get("content") or {}).get("video_url") or "")
    if not content_url:
        raise RuntimeError(
            f"Seedance video task {job_id} succeeded without content.video_url"
        )
    # The returned URL is pre-signed object storage; do not forward the Ark token.
    content_response = client.get(content_url, timeout=120)
    content_response.raise_for_status()
    content = content_response.content
    if not content or len(content) > 200 * 1024 * 1024:
        raise RuntimeError("Seedance video is empty or exceeds 200MB")
    mime_type = content_response.headers.get("content-type", "video/mp4").split(";")[0]
    last_frame = _download_url_bytes(client, (job.get("content") or {}).get("last_frame_url"))
    return GeneratedVideo(
        job_id, content, mime_type, job, degraded_to_text, last_frame
    )


def _resumable_job(
    progress_path: Path | None,
    payload: dict[str, object],
    *,
    replace_existing_job: bool = False,
) -> dict[str, Any] | None:
    if replace_existing_job or progress_path is None or not progress_path.is_file():
        return None
    try:
        record = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stored = str(record.get("request_fingerprint") or "")
    expected = _request_fingerprint(payload)
    if stored and stored != expected:
        return None
    job = record.get("job") or {}
    same_model = record.get("model") == payload.get("model")
    status = str(job.get("status", "")).lower()
    # A succeeded job is also resumable: its task endpoint still exposes the
    # signed download URL. Keeping it avoids submitting and charging for the
    # same Shot again when a later Shot or local render interrupted the run.
    # Legacy progress files have no fingerprint; same-model resume still
    # applies so an interrupted first pass is not billed twice. Repair must
    # pass replace_existing_job so that path cannot reuse an old clip.
    if same_model and job.get("id") and status not in {
        "failed", "cancelled", "canceled", "expired"
    }:
        return job
    return None


def _write_progress(
    progress_path: Path | None,
    payload: dict[str, object],
    job: dict[str, Any],
) -> None:
    if progress_path is None:
        return
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(
        json.dumps(
            {
                "model": payload.get("model"),
                "request_fingerprint": _request_fingerprint(payload),
                "updated_at": datetime.now(UTC).isoformat(),
                "job": job,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _request_fingerprint(payload: dict[str, object]) -> str:
    hasher = hashlib.sha256()
    for key in (
        "model",
        "prompt",
        "duration",
        "aspect_ratio",
        "resolution",
        "generate_audio",
    ):
        hasher.update(str(payload.get(key) or "").encode("utf-8"))
        hasher.update(b"\0")
    for frame in payload.get("frame_images") or []:
        _hash_media_item(hasher, frame, "image_url")
    for audio in payload.get("audio_urls") or []:
        _hash_media_item(hasher, audio, "audio_url")
    return hasher.hexdigest()


def _hash_media_item(hasher: Any, item: object, url_key: str) -> None:
    if not isinstance(item, dict):
        return
    blob = item.get(url_key)
    url = ""
    if isinstance(blob, dict):
        url = str(blob.get("url") or "")
    hasher.update(hashlib.sha256(url.encode("utf-8")).digest())
    hasher.update(str(item.get("role") or item.get("frame_type") or "").encode())
    hasher.update(b"\0")


def seedance_api_config(
    settings: AssetGenerationSettings,
) -> tuple[str, str]:
    if not settings.video_api_key or not settings.video_base_url:
        raise ValueError(
            "Missing Seedance video API configuration. Set "
            "UGC_VIDEO_API_KEY and UGC_VIDEO_BASE_URL."
        )
    base_url = settings.video_base_url.rstrip("/")
    for suffix in ("/chat/completions", "/contents/generations/tasks"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    return settings.video_api_key, base_url


def _ark_payload(payload: dict[str, object]) -> dict[str, object]:
    prompt = str(payload.get("prompt") or "").strip()
    ratio = str(payload.get("aspect_ratio") or "9:16")
    duration = int(payload.get("duration") or 5)
    extras = f"--ratio {ratio} --dur {duration}"
    resolution = str(payload.get("resolution") or "").strip()
    if resolution:
        extras += f" --rs {resolution}"
    content: list[dict[str, object]] = [
        {"type": "text", "text": f"{prompt} {extras}"}
    ]
    for frame in payload.get("frame_images") or []:
        item = _content_media_item(frame, "image_url")
        if item is not None:
            content.append(item)
    for audio in payload.get("audio_urls") or []:
        item = _content_media_item(audio, "audio_url")
        if item is not None:
            content.append(item)
    body: dict[str, object] = {
        "model": payload["model"],
        "content": content,
        "return_last_frame": True,
    }
    if "generate_audio" in payload:
        body["generate_audio"] = bool(payload["generate_audio"])
    return body


def _content_media_item(
    frame: object,
    url_key: str,
) -> dict[str, object] | None:
    if not isinstance(frame, dict):
        return None
    blob = frame.get(url_key)
    if not (isinstance(blob, dict) and blob.get("url")):
        return None
    item: dict[str, object] = {
        "type": url_key,
        url_key: {"url": str(blob["url"])},
    }
    # Ark distinguishes first_frame / last_frame / reference_image via
    # the "role" field, and those modes cannot be mixed in one request.
    # A role-less single image defaults to first frame, so omitting the
    # key preserves the legacy behaviour. Audio uses role=reference_audio.
    role = frame.get("role") or frame.get("frame_type")
    if role:
        item["role"] = str(role)
    return item


def _has_inline_media(payload: dict[str, object]) -> bool:
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        for key in ("image_url", "audio_url"):
            blob = item.get(key)
            if isinstance(blob, dict) and str(blob.get("url", "")).startswith("data:"):
                return True
    return False


def last_frame_bytes_from_job(job_path: Path) -> bytes | None:
    if not job_path.is_file():
        return None
    try:
        record = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = ((record.get("job") or {}).get("content") or {}).get("last_frame_url")
    if not url:
        return None
    with httpx.Client() as client:
        try:
            payload = _download_url_bytes(client, url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Seedance 尾帧下载失败: {url}") from exc
    return payload


def _download_url_bytes(client: httpx.Client, url: object) -> bytes | None:
    text = str(url or "").strip()
    if not text:
        return None
    response = client.get(text, timeout=60)
    response.raise_for_status()
    payload = response.content
    if not payload:
        return None
    return payload


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
