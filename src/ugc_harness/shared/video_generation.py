from __future__ import annotations

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


def generate_seedance_video(
    client: httpx.Client,
    settings: AssetGenerationSettings,
    payload: dict[str, object],
    *,
    progress_path: Path | None = None,
    allow_text_fallback: bool = True,
) -> GeneratedVideo:
    """Submit and download a Seedance video through Volcengine Ark's task API.

    When ``allow_text_fallback`` is False, a 400 response for a request that
    carries inline reference images raises instead of silently retrying as
    text-to-video. Character-consistency requests must set it to False, since
    dropping the reference image would let the model reinvent the character.
    """
    api_key, base_url = seedance_api_config(settings)
    degraded_to_text = False
    job = _resumable_job(progress_path, payload)
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
    return GeneratedVideo(job_id, content, mime_type, job, degraded_to_text)


def _resumable_job(
    progress_path: Path | None,
    payload: dict[str, object],
) -> dict[str, Any] | None:
    if progress_path is None or not progress_path.is_file():
        return None
    try:
        record = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    job = record.get("job") or {}
    same_model = record.get("model") == payload.get("model")
    status = str(job.get("status", "")).lower()
    # A succeeded job is also resumable: its task endpoint still exposes the
    # signed download URL. Keeping it avoids submitting and charging for the
    # same Shot again when a later Shot or local render interrupted the run.
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
                "updated_at": datetime.now(UTC).isoformat(),
                "job": job,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


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
    content: list[dict[str, object]] = [
        {"type": "text", "text": f"{prompt} --ratio {ratio} --dur {duration}"}
    ]
    for frame in payload.get("frame_images") or []:
        if not isinstance(frame, dict):
            continue
        image_url = frame.get("image_url")
        if isinstance(image_url, dict) and image_url.get("url"):
            item: dict[str, object] = {
                "type": "image_url",
                "image_url": {"url": str(image_url["url"])},
            }
            # Ark distinguishes first_frame / last_frame / reference_image via
            # the "role" field; a role-less single image defaults to first
            # frame, so omitting the key preserves the legacy behaviour.
            role = frame.get("role") or frame.get("frame_type")
            if role:
                item["role"] = str(role)
            content.append(item)
    return {
        "model": payload["model"],
        "content": content,
        "return_last_frame": True,
    }


def _has_inline_media(payload: dict[str, object]) -> bool:
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url")
        if isinstance(image_url, dict) and str(image_url.get("url", "")).startswith(
            "data:"
        ):
            return True
    return False


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
