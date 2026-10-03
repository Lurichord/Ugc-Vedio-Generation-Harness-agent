from __future__ import annotations

import json
from pathlib import Path

from .corpus import knowledge_root
from .models import IndexedChunk

INDEX_DIR = ".index"
INDEX_FILE = "index.json"


def index_path(root: str | Path | None = None) -> Path:
    return knowledge_root(root) / INDEX_DIR / INDEX_FILE


def save_index(
    chunks: list[IndexedChunk],
    *,
    root: str | Path | None = None,
    model: str,
) -> Path:
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "chunks": [item.model_dump(mode="json") for item in chunks],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_index(root: str | Path | None = None) -> list[IndexedChunk]:
    path = index_path(root)
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("chunks") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return [IndexedChunk.model_validate(item) for item in items]
