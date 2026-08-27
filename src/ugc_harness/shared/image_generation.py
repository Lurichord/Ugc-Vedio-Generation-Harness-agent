from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from .settings import AssetGenerationSettings


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    mime_type: str
    response: dict[str, Any]


def generate_seedream_image(
    client: httpx.Client,
    settings: AssetGenerationSettings,
    prompt: str,
) -> GeneratedImage:
    if not settings.image_api_key:
        raise ValueError(
            "Missing Seedream API key. Set UGC_IMAGE_API_KEY or "
            "VOLCENGINE_ARK_API_KEY."
        )
    response = client.post(
        settings.image_base_url.rstrip("/") + "/images/generations",
        headers={
            "Authorization": f"Bearer {settings.image_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.image_model,
            "prompt": prompt,
            "size": "1440x2560",
            "response_format": "url",
            "watermark": False,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    item = payload["data"][0]
    encoded = item.get("b64_json")
    if encoded:
        content = base64.b64decode(encoded, validate=True)
        mime_type = item.get("media_type") or "image/png"
    else:
        image_url = str(item.get("url") or "")
        if not image_url.startswith(("https://", "http://")):
            raise RuntimeError("Seedream response has no usable image URL")
        downloaded = client.get(image_url, timeout=120)
        downloaded.raise_for_status()
        content = downloaded.content
        mime_type = downloaded.headers.get("content-type", "image/jpeg").split(";")[0]
    if not content or len(content) > 30 * 1024 * 1024:
        raise RuntimeError("Seedream image is empty or exceeds 30MB")
    return GeneratedImage(content, mime_type, payload)
