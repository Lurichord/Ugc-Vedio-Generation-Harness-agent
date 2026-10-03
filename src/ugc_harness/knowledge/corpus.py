from __future__ import annotations

import json
import re
from pathlib import Path

from .models import CollectionSpec, KnowledgeChunk

COLLECTIONS_FILE = "collections.json"
COLLECTIONS_DIR = "collections"

_MIN_CHARS = 80
_MAX_CHARS = 500
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")


def visible_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def knowledge_root(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "knowledge"


def load_collections(root: str | Path | None = None) -> list[CollectionSpec]:
    catalog = knowledge_root(root) / COLLECTIONS_FILE
    if not catalog.is_file():
        return []
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    items = payload.get("collections") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("collections.json must contain a collections list")
    return [CollectionSpec.model_validate(item) for item in items]


def load_chunks(root: str | Path | None = None) -> list[KnowledgeChunk]:
    base = knowledge_root(root)
    collections = load_collections(base)
    chunks: list[KnowledgeChunk] = []
    for spec in collections:
        folder = base / COLLECTIONS_DIR / spec.id
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            relative = f"{spec.id}/{path.name}"
            chunks.extend(
                chunk_markdown(
                    path.read_text(encoding="utf-8"),
                    collection_id=spec.id,
                    source=relative,
                )
            )
    return chunks


def chunk_markdown(
    markdown: str,
    *,
    collection_id: str,
    source: str,
) -> list[KnowledgeChunk]:
    raw_sections = _split_headings(markdown)
    expanded: list[tuple[str, str]] = []
    for heading, body in raw_sections:
        if visible_char_count(body) > _MAX_CHARS and _has_subheading(body):
            sub_sections = _split_headings(body, min_level=3)
            if sub_sections:
                for sub_heading, sub_body in sub_sections:
                    title = (
                        f"{heading} / {sub_heading}" if heading and sub_heading else (heading or sub_heading)
                    )
                    expanded.append((title, sub_body))
            else:
                expanded.append((heading, body))
        else:
            expanded.append((heading, body))

    merged: list[tuple[str, str]] = []
    for heading, body in expanded:
        body = body.strip()
        if not body:
            continue
        if merged and visible_char_count(body) < _MIN_CHARS:
            prev_heading, prev_body = merged[-1]
            merged[-1] = (prev_heading, prev_body + "\n\n" + body)
        else:
            merged.append((heading, body))

    chunks: list[KnowledgeChunk] = []
    for index, (heading, body) in enumerate(merged, start=1):
        embed_text = f"{heading}。{body}" if heading else body
        slug = _slug(heading) or f"s{index:02d}"
        chunks.append(
            KnowledgeChunk(
                chunk_id=f"{collection_id}:{Path(source).name}#{slug}",
                collection_id=collection_id,
                source=source,
                heading=heading,
                text=body,
                embed_text=embed_text,
            )
        )
    return chunks


def _split_headings(
    markdown: str,
    *,
    min_level: int = 2,
) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    started = False
    for raw in markdown.splitlines():
        match = _HEADING.match(raw)
        if match:
            level = len(match.group(1))
            if level == 1:
                continue
            if level < min_level:
                continue
            if started:
                sections.append((heading, "\n".join(lines).strip()))
            heading = match.group(2).strip()
            lines = []
            started = True
            continue
        if started:
            lines.append(raw)
        elif raw.strip():
            started = True
            heading = ""
            lines.append(raw)
    if started:
        sections.append((heading, "\n".join(lines).strip()))
    return [(title, body) for title, body in sections if body.strip()]


def _has_subheading(body: str) -> bool:
    return any(
        match and len(match.group(1)) >= 3
        for line in body.splitlines()
        if (match := _HEADING.match(line))
    )


def _slug(heading: str) -> str:
    cleaned = re.sub(r"\s+", "", heading)
    return cleaned[:40]
