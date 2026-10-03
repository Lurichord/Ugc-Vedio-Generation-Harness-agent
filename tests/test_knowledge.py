from __future__ import annotations

import math
import shutil
from pathlib import Path

from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.knowledge.corpus import (
    chunk_markdown,
    load_chunks,
    load_collections,
    visible_char_count,
)
from ugc_harness.knowledge.format import (
    PRIOR_CLOSER,
    narrative_agent_context,
    narrative_writer_system,
    prepend_prior_knowledge,
    prior_knowledge_text,
)
from ugc_harness.knowledge.models import KnowledgeSnippet, PriorKnowledge
from ugc_harness.knowledge.retrieve import (
    build_index,
    query_text,
    retrieve_prior_knowledge,
)
from ugc_harness.knowledge.route import route_collection_ids
from ugc_harness.shared.settings import EmbeddingSettings


REPO_KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"


class HashEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_bag_vector(text) for text in texts]


class CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [_bag_vector(text) for text in texts]


def _bag_vector(text: str, dim: int = 64) -> list[float]:
    vec = [0.0] * dim
    for char in text:
        vec[ord(char) % dim] += 1.0
    norm = math.sqrt(sum(item * item for item in vec)) or 1.0
    return [item / norm for item in vec]


def _copy_corpus(tmp_path: Path) -> Path:
    dest = tmp_path / "knowledge"
    shutil.copytree(REPO_KNOWLEDGE, dest, ignore=shutil.ignore_patterns(".index"))
    return dest


def _thresholds() -> EmbeddingSettings:
    return EmbeddingSettings(
        api_key="test-key-1234",
        base_url="https://example.invalid",
        model="fake",
        min_score=0.05,
        collection_min_score=0.2,
    )


def test_query_text_uses_topic_and_goal_only() -> None:
    brief = make_brief(
        topic="中秋节科普",
        goal="让观众知道中秋节的历史来源",
        production_mode="explainer",
    )
    text = query_text(brief)
    assert text == "中秋节科普。让观众知道中秋节的历史来源。"
    assert "explainer" not in text
    assert "主题" not in text
    assert brief.audience.description not in text


def test_chunk_markdown_splits_on_h2() -> None:
    body_a = "这段说明按二级标题切开。" * 12
    body_b = "另一段同样足够长的正文用来保证不会被合并。" * 12
    chunks = chunk_markdown(
        f"# 标题\n\n## 第一节\n{body_a}\n\n## 第二节\n{body_b}\n",
        collection_id="demo",
        source="demo/a.md",
    )
    assert [item.heading for item in chunks] == ["第一节", "第二节"]
    assert chunks[0].embed_text.startswith("第一节。")


def test_chunk_markdown_merges_short_sections() -> None:
    chunks = chunk_markdown(
        "## 长节\n" + ("完整意思。" * 20) + "\n\n## 短\n太短了。\n",
        collection_id="demo",
        source="demo/a.md",
    )
    assert len(chunks) == 1
    assert "太短了" in chunks[0].text


def test_repo_corpus_chunks_have_reasonable_size() -> None:
    chunks = load_chunks(REPO_KNOWLEDGE)
    assert len(chunks) >= 8
    sizes = [visible_char_count(item.text) for item in chunks]
    assert min(sizes) >= 40
    assert any("origin.md" in item.source for item in chunks)


def test_route_zhongqiu_alias() -> None:
    collections = load_collections(REPO_KNOWLEDGE)
    assert route_collection_ids("中秋节科普", collections) == ["zhongqiu"]


def test_route_yuebing_includes_zhongqiu() -> None:
    collections = load_collections(REPO_KNOWLEDGE)
    assert route_collection_ids("做月饼", collections) == ["yuebing", "zhongqiu"]


def test_route_unrelated_topic_is_empty() -> None:
    collections = load_collections(REPO_KNOWLEDGE)
    assert route_collection_ids("量子力学入门", collections) == []


def test_empty_corpus_does_not_call_embedder(tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    brief = make_brief(topic="中秋节科普", goal="让观众知道中秋节的历史来源")
    prior = retrieve_prior_knowledge(
        brief,
        root=tmp_path,
        embedder=embedder,
        settings=_thresholds(),
    )
    assert prior.snippets == []
    assert embedder.calls == 0


def test_retrieve_returns_origin_for_history_brief(tmp_path: Path) -> None:
    root = _copy_corpus(tmp_path)
    embedder = HashEmbedder()
    build_index(root=root, embedder=embedder, model="fake")
    brief = make_brief(
        topic="中秋节科普",
        goal="让观众知道中秋节的历史来源",
    )
    prior = retrieve_prior_knowledge(
        brief,
        root=root,
        embedder=HashEmbedder(),
        settings=_thresholds(),
    )
    assert prior.collection_ids == ["zhongqiu"]
    assert prior.snippets
    assert any("origin.md" in item.source for item in prior.snippets)
    assert all(item.collection_id == "zhongqiu" for item in prior.snippets)


def test_retrieve_yuebing_also_searches_zhongqiu(tmp_path: Path) -> None:
    root = _copy_corpus(tmp_path)
    build_index(root=root, embedder=HashEmbedder(), model="fake")
    brief = make_brief(topic="做月饼", goal="按步骤做出月饼")
    prior = retrieve_prior_knowledge(
        brief,
        root=root,
        embedder=HashEmbedder(),
        settings=_thresholds(),
    )
    assert "yuebing" in prior.collection_ids
    assert "zhongqiu" in prior.collection_ids


def test_prior_knowledge_omitted_when_empty() -> None:
    brief = make_brief(topic="中秋节科普", goal="让观众知道中秋节的历史来源")
    context = narrative_agent_context(brief, PriorKnowledge.empty("q"))
    assert "prior_knowledge" not in context
    assert prepend_prior_knowledge("写章节", {}) == "写章节"


def test_prior_knowledge_injected_into_agent_context_and_prompt() -> None:
    brief = make_brief(topic="中秋节科普", goal="让观众知道中秋节的历史来源")
    prior = PriorKnowledge(
        query=query_text(brief),
        collection_ids=["zhongqiu"],
        snippets=[
            KnowledgeSnippet(
                id="zhongqiu:origin.md#祭月与时令",
                text="赏月、祭月可以追溯到秋分前后的月神祭祀。",
                source="zhongqiu/origin.md",
                score=0.8,
                collection_id="zhongqiu",
            )
        ],
    )
    context = narrative_agent_context(brief, prior)
    assert context["prior_knowledge"]["snippets"][0]["source"] == "zhongqiu/origin.md"
    decorated = prepend_prior_knowledge("写章节", {"prior_knowledge": prior_knowledge_text(prior)})
    assert "知识库" in decorated
    assert "语料参考" in decorated
    assert "优先参考" not in decorated
    assert "秋分" in decorated
    assert decorated.startswith("写章节")
    assert decorated.endswith(PRIOR_CLOSER)
    system = narrative_writer_system()
    assert "CreativeBrief" in system
    assert "可以整段不用" in system
    assert "资料与 Brief 冲突时跟 Brief" in system


def test_controller_context_builder_passes_prior() -> None:
    from ugc_harness.harness.controller import NarrativeHarnessController

    class DummyModel:
        settings = None

        def decide(self, messages: object, tools: object) -> object:
            raise RuntimeError("unused")

    controller = NarrativeHarnessController.from_mcp(DummyModel(), "demo-model")
    brief = make_brief(topic="中秋节科普", goal="让观众知道中秋节的历史来源")
    prior = PriorKnowledge(
        query=query_text(brief),
        collection_ids=["zhongqiu"],
        snippets=[
            KnowledgeSnippet(
                id="c1",
                text="祭月",
                source="zhongqiu/origin.md",
                score=0.9,
                collection_id="zhongqiu",
            )
        ],
    )
    task = controller.create_task(brief)
    context = controller.agent.context_builder(
        task, {"brief": brief, "prior_knowledge": prior}
    )
    assert "prior_knowledge" in context["memory"]
    assert context["memory"]["prior_knowledge"]["snippets"][0]["text"] == "祭月"
    assert "identities" in context["memory"]
    assert "shots" in context["memory"]
    assert context["turn"]["instruction"]


def test_vision_embedding_uses_multimodal_payload() -> None:
    from ugc_harness.knowledge.embed import (
        _embedding_from_payload,
        _uses_multimodal_endpoint,
    )

    assert _uses_multimodal_endpoint("doubao-embedding-vision-251215")
    assert not _uses_multimodal_endpoint("doubao-embedding-text-240515")
    vector = _embedding_from_payload({"data": {"embedding": [0.1, 0.2]}})
    assert vector == [0.1, 0.2]
