from __future__ import annotations

import math
import re

from .models import CollectionSpec


def normalize_topic(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def route_collection_ids(
    topic: str,
    collections: list[CollectionSpec],
    *,
    query_vector: list[float] | None = None,
    card_vectors: dict[str, list[float]] | None = None,
    collection_min_score: float = 0.4,
) -> list[str]:
    matched = _alias_and_keyword_hits(topic, collections)
    if matched:
        return _with_related(matched, collections)
    if query_vector is None or not card_vectors:
        return []
    scored: list[tuple[float, str]] = []
    for spec in collections:
        vector = card_vectors.get(spec.id)
        if not vector:
            continue
        score = cosine_similarity(query_vector, vector)
        if score >= collection_min_score:
            scored.append((score, spec.id))
    scored.sort(reverse=True)
    return _with_related([item_id for _, item_id in scored[:2]], collections)


def _alias_and_keyword_hits(
    topic: str,
    collections: list[CollectionSpec],
) -> list[str]:
    needle = normalize_topic(topic)
    if not needle:
        return []
    alias_hits: list[str] = []
    keyword_hits: list[str] = []
    for spec in collections:
        aliases = [spec.title, *spec.aliases]
        keywords = list(spec.keywords)
        if any(
            token and (token in needle or needle in token)
            for token in (normalize_topic(name) for name in aliases)
        ):
            alias_hits.append(spec.id)
        elif any(
            token and token in needle
            for token in (normalize_topic(name) for name in keywords)
        ):
            keyword_hits.append(spec.id)
    return alias_hits + [item for item in keyword_hits if item not in alias_hits]


def _with_related(
    collection_ids: list[str],
    collections: list[CollectionSpec],
) -> list[str]:
    by_id = {spec.id: spec for spec in collections}
    ordered: list[str] = []
    for collection_id in collection_ids:
        if collection_id not in by_id:
            continue
        if collection_id not in ordered:
            ordered.append(collection_id)
        for related in by_id[collection_id].related:
            if related in by_id and related not in ordered:
                ordered.append(related)
    return ordered


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)
