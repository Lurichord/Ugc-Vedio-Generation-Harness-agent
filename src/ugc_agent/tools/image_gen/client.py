from __future__ import annotations

import base64
from pathlib import Path

from ..ark import data_url, join_url, request_json, require_env, write_unique_file


def generate_image(
    prompt: str,
    *,
    reference_paths: list[str] | None = None,
    size: str = "2K",
) -> bytes:
    api_key = require_env("VOLCENGINE_ARK_API_KEY")
    base_url = require_env("UGC_IMAGE_BASE_URL", "VOLCENGINE_ARK_BASE_URL")
    model = require_env("UGC_IMAGE_MODEL")
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "watermark": False,
        "response_format": "b64_json",
        "size": size,
    }
    if reference_paths:
        payload["image"] = [data_url(path) for path in reference_paths]
    result = request_json(
        join_url(base_url, "images/generations"),
        api_key=api_key,
        payload=payload,
        timeout=180,
    )
    items = result.get("data") or []
    if not items:
        raise RuntimeError("image-gen returned no image")
    encoded = items[0].get("b64_json") or ""
    if not encoded:
        raise RuntimeError("image-gen returned no b64_json")
    if "," in encoded and encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    return base64.b64decode(encoded)


def write_image(directory: Path, data: bytes) -> Path:
    return write_unique_file(directory, ".png", data)
