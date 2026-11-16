from __future__ import annotations

from pathlib import Path

from ..ark import data_url, join_url, request_json, require_env

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


def _media_part(path: str) -> dict:
    url = path if path.startswith(("http://", "https://")) else data_url(path)
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return {"type": "video_url", "video_url": {"url": url}}
    if suffix in IMAGE_SUFFIXES or path.startswith(("http://", "https://")):
        return {"type": "image_url", "image_url": {"url": url}}
    raise ValueError(f"unsupported media file: {path}")


def describe_media(prompt: str, media_paths: list[str] | None = None) -> str:
    api_key = require_env("UGC_LLM_API_KEY")
    base_url = require_env("UGC_LLM_BASE_URL", "VOLCENGINE_ARK_BASE_URL")
    model = require_env("UGC_LLM_MODEL")
    content: list[dict] = [{"type": "text", "text": prompt}]
    for path in media_paths or []:
        content.append(_media_part(path))
    result = request_json(
        join_url(base_url, "chat/completions"),
        api_key=api_key,
        payload={
            "model": model,
            "messages": [{"role": "user", "content": content}],
        },
        timeout=180,
    )
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("vision-understanding returned no choices")
    message = (choices[0].get("message") or {}).get("content")
    if not message:
        raise RuntimeError("vision-understanding returned empty content")
    if isinstance(message, list):
        texts = [
            item.get("text", "")
            for item in message
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in texts if part).strip()
    return str(message).strip()
