from __future__ import annotations

from ..agents.narrative_agent.models import CreativeBrief
from .models import PriorKnowledge

NARRATIVE_WRITER_SYSTEM = """你在写 UGC 短视频叙事的一层结构化产物。

设计权只属于用户消息里的 CreativeBrief：主题、目标、受众、时长、体裁和约束以 Brief 字段为准。
若消息里有知识库资料，那只是语料参考，可以整段不用。
不要为了用上资料而改人物目标、冲突、结构、步骤或收束。
资料与 Brief 冲突时跟 Brief。
资料没有覆盖的内容不要编造成确切事实。
只返回有效 JSON，不要 Markdown，不要解释。
"""

PRIOR_PREAMBLE = (
    "以下资料来自知识库，只作语料参考，可以整段不用。"
    "若借用，只取能服务 Brief 的说法或细节，不要把资料当成大纲或必须讲完的知识点。"
    "主题、结构、钩子、论证路径、人物目标和收束必须服从 CreativeBrief。"
    "资料与 Brief 冲突时跟 Brief。"
    "资料没有覆盖的内容不要编造成确切事实。"
)

PRIOR_CLOSER = (
    "知识库资料到此结束。现在只按用户消息开头的 CreativeBrief 完成本层产物；"
    "资料不能直接服务 Brief，或会改设计，就整段忽略。"
)


def narrative_writer_system() -> str:
    return NARRATIVE_WRITER_SYSTEM


def prior_knowledge_text(prior: PriorKnowledge | None) -> str:
    if prior is None or not prior.snippets:
        return ""
    blocks = [
        f"[{index}] {item.source}\n{item.text}"
        for index, item in enumerate(prior.snippets, start=1)
    ]
    return "\n\n".join(blocks)


def narrative_agent_context(
    brief: CreativeBrief,
    prior: PriorKnowledge | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "creative_brief": brief.model_dump(mode="json"),
    }
    if prior is not None and prior.snippets:
        payload["prior_knowledge"] = prior.model_dump(mode="json")
    return payload


def merge_generation_context(
    base: dict[str, object] | None,
    prior: PriorKnowledge | None,
) -> dict[str, object]:
    merged = dict(base or {})
    text = prior_knowledge_text(prior)
    if text:
        merged["prior_knowledge"] = text
    return merged


def prepend_prior_knowledge(prompt: str, generation_context: dict) -> str:
    prior = generation_context.get("prior_knowledge")
    if not isinstance(prior, str) or not prior.strip():
        return prompt
    return f"{prompt}\n\n{PRIOR_PREAMBLE}\n\n{prior.strip()}\n\n{PRIOR_CLOSER}"
