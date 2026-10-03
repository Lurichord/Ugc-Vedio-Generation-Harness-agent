from __future__ import annotations

import logging
from pathlib import Path

from ..agents.narrative_agent.models import CreativeBrief
from .corpus import load_collections, load_chunks
from .embed import ArkEmbedder, Embedder
from ..shared.settings import EmbeddingSettings
from .models import IndexedChunk, KnowledgeSnippet, PriorKnowledge
from .route import cosine_similarity, route_collection_ids
from .store import load_index

logger = logging.getLogger(__name__)

_MAX_PER_SOURCE = 2


def query_text(brief: CreativeBrief) -> str:
    topic = brief.topic.strip()
    goal = brief.communication.goal.strip()
    return f"{topic}。{goal}。"


def retrieve_prior_knowledge(
    brief: CreativeBrief,
    *,
    root: str | Path | None = None,
    embedder: Embedder | None = None,
    settings: EmbeddingSettings | None = None,
    chunks: list[IndexedChunk] | None = None,
) -> PriorKnowledge:
    query = query_text(brief)
    try:
        return _retrieve(
            brief,
            query=query,
            root=root,
            embedder=embedder,
            settings=settings,
            chunks=chunks,
        )
    except Exception:
        logger.exception("knowledge retrieve failed")
        return PriorKnowledge.empty(query)


def _retrieve(
    brief: CreativeBrief,
    *,
    query: str,
    root: str | Path | None,
    embedder: Embedder | None,
    settings: EmbeddingSettings | None,
    chunks: list[IndexedChunk] | None,
) -> PriorKnowledge:
    collections = load_collections(root)
    if not collections:
        return PriorKnowledge.empty(query)

    indexed = chunks if chunks is not None else load_index(root)
    if not indexed:
        logger.warning("knowledge retrieve skipped: index is missing")
        return PriorKnowledge.empty(query)

    active_embedder = embedder
    active_settings = settings
    if active_embedder is None:
        active_settings = active_settings or EmbeddingSettings.try_from_environment()
        if active_settings is None:
            logger.warning("knowledge retrieve skipped: embedding settings missing")
            return PriorKnowledge.empty(query)
        active_embedder = ArkEmbedder(active_settings)
    if active_settings is None:
        active_settings = EmbeddingSettings.try_from_environment() or _default_thresholds()

    collection_ids = route_collection_ids(brief.topic, collections)
    query_vector = active_embedder.embed([query])[0]
    if not collection_ids:
        card_vectors = {
            spec.id: active_embedder.embed([spec.card_text()])[0]
            for spec in collections
        }
        collection_ids = route_collection_ids(
            brief.topic,
            collections,
            query_vector=query_vector,
            card_vectors=card_vectors,
            collection_min_score=active_settings.collection_min_score,
        )
    if not collection_ids:
        return PriorKnowledge.empty(query)

    allowed = set(collection_ids)
    ranked: list[tuple[float, IndexedChunk]] = []
    for chunk in indexed:
        if chunk.collection_id not in allowed:
            continue
        score = cosine_similarity(query_vector, chunk.vector)
        if score >= active_settings.min_score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)

    snippets: list[KnowledgeSnippet] = []
    per_source: dict[str, int] = {}
    for score, chunk in ranked[: active_settings.candidate_k]:
        taken = per_source.get(chunk.source, 0)
        if taken >= _MAX_PER_SOURCE:
            continue
        per_source[chunk.source] = taken + 1
        snippets.append(
            KnowledgeSnippet(
                id=chunk.chunk_id,
                text=chunk.text,
                source=chunk.source,
                score=round(score, 4),
                collection_id=chunk.collection_id,
            )
        )
        if len(snippets) >= active_settings.top_k:
            break
    return PriorKnowledge(
        query=query,
        collection_ids=collection_ids,
        snippets=snippets,
    )


def build_index(
    *,
    root: str | Path | None = None,
    embedder: Embedder,
    model: str,
) -> list[IndexedChunk]:
    from .store import save_index

    raw = load_chunks(root)
    if not raw:
        save_index([], root=root, model=model)
        return []
    vectors = embedder.embed([item.embed_text for item in raw])
    indexed = [
        IndexedChunk(**item.model_dump(), vector=vector)
        for item, vector in zip(raw, vectors)
    ]
    save_index(indexed, root=root, model=model)
    return indexed


def _default_thresholds() -> EmbeddingSettings:
    return EmbeddingSettings(
        api_key="not-used-0000",
        base_url="https://example.invalid",
        model="fake",
    )
