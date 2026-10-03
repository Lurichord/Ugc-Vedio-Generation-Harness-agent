from __future__ import annotations

from typing import Any, Protocol

import httpx
from openai import OpenAI

from ..shared.settings import EmbeddingSettings

__all__ = ["Embedder", "EmbeddingSettings", "ArkEmbedder"]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ArkEmbedder:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if _uses_multimodal_endpoint(self.settings.model):
            return [_embed_one_multimodal(self.settings, text) for text in texts]
        response = self.client.embeddings.create(
            model=self.settings.model,
            input=texts,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


def _uses_multimodal_endpoint(model: str) -> bool:
    name = model.casefold()
    return "vision" in name or "multimodal" in name


def _embed_one_multimodal(settings: EmbeddingSettings, text: str) -> list[float]:
    response = httpx.post(
        f"{settings.base_url.rstrip('/')}/embeddings/multimodal",
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.model,
            "encoding_format": "float",
            "input": [{"type": "text", "text": text}],
        },
        timeout=settings.timeout_seconds,
    )
    response.raise_for_status()
    return _embedding_from_payload(response.json())


def _embedding_from_payload(payload: dict[str, Any]) -> list[float]:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("embedding"), list):
        return [float(item) for item in data["embedding"]]
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("embedding"), list):
            return [float(item) for item in first["embedding"]]
    raise ValueError("multimodal embedding response is missing a vector")
