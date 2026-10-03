"""Knowledge-base models for narrative prior retrieval."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CollectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)

    def card_text(self) -> str:
        names = [self.title, *self.aliases, *self.keywords]
        return "、".join(item for item in names if item.strip())


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    collection_id: str
    source: str
    heading: str
    text: str
    embed_text: str


class IndexedChunk(KnowledgeChunk):
    vector: list[float] = Field(min_length=1)


class KnowledgeSnippet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    source: str
    score: float
    collection_id: str


class PriorKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = ""
    collection_ids: list[str] = Field(default_factory=list)
    snippets: list[KnowledgeSnippet] = Field(default_factory=list)

    @classmethod
    def empty(cls, query: str = "") -> "PriorKnowledge":
        return cls(query=query)
