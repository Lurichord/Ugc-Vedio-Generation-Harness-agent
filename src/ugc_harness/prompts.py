from __future__ import annotations

import json

from .models import CreativeBrief, PlanningArtifact, ScriptArtifact


SYSTEM_PROMPT = """你是 UGC 短视频内容总编。你制作的是由口播驱动、信息和情绪推进、
异构视觉素材承载的 UGC，不是电影、广告片或分镜文学。

工作原则：
1. Beat 是最小认知推进单位，不等同于句子或镜头。
2. 先规划 Planned Beat，再写口播；不得先写长文再拆段。
3. Hook–Body–Close 只作为 Section 层，Beat 使用更细的 discourse role。
4. 每个 Beat 必须改变观众的知识、信念、问题或情绪。
5. 数字、新闻、研究结论、引用等事实性主张必须标记 evidence_need。
6. 不能虚构亲身经历、采访、数据或来源。
7. 文风口语、直接、可说出口；避免论文腔、空洞热词和电影化场景描述。
8. 只返回有效 JSON，不要 Markdown，不要解释。
"""


def planning_prompt(brief: CreativeBrief) -> str:
    target_ms = brief.target.duration_target_ms
    min_beats = max(8, round(target_ms / 8_000))
    max_beats = min(20, round(target_ms / 5_000))
    return f"""请为以下 CreativeBrief 生成内容结构。

CreativeBrief:
{brief.model_dump_json(indent=2)}

硬性要求：
- 选择一个适合主题的叙事模式，如 Question→Evidence→Explanation→Implication、
  Myth→Evidence→Correction 或 Result→Reverse Engineering→Lesson。
- 严格生成 3 个 Section，role 依次为 hook、body、close。
- 生成 {min_beats}–{max_beats} 个 Planned Beat，order 从 1 连续递增。
- 所有 Section 的 target_duration_ms 总和接近 {target_ms}。
- 所有 Beat 的 target_duration_ms 总和接近 {target_ms}，误差不超过 8%。
- Hook 在前 2 个 Beat 建立信息缺口；中段至少有一次 attention reset；
  Close 必须兑现 Hook，不能只喊口号。
- opening 只能用于首个 Beat；其他 Beat 明确与前一 Beat 的语义关系。
- factual claim 必须将 evidence_need.required 设为 true，并写清待核实主张。
- visual_intent_hint 只描述视觉功能方向，不绑定具体素材。

返回对象必须严格符合此结构，不得添加字段：
{{
  "narrative_pattern": "string",
  "one_sentence_thesis": "string",
  "sections": [
    {{
      "section_id": "section_hook",
      "role": "hook",
      "target_duration_ms": 8000,
      "goal": "string",
      "attention_strategy": "string"
    }}
  ],
  "beats": [
    {{
      "planned_beat_id": "pb01",
      "section_id": "section_hook",
      "order": 1,
      "semantic_goal": "string",
      "discourse_role": "question",
      "relation_to_previous": "opening",
      "target_effect": "string",
      "target_duration_ms": 5000,
      "audience_delta": {{
        "knowledge_added": [],
        "belief_update": null,
        "question_added": "string or null",
        "question_resolved": null,
        "emotion_target": "curiosity"
      }},
      "evidence_need": {{
        "required": false,
        "claim_to_verify": null,
        "acceptable_source_types": []
      }},
      "visual_intent_hint": "string"
    }}
  ]
}}

枚举：
- discourse_role: question, expected_answer, reveal, claim, evidence, explanation,
  example, contrast, deepening, implication, payoff, callback
- relation_to_previous: opening, continuation, cause, contrast, escalation,
  evidence_for, example_of, resolution, callback
"""


def script_prompt(brief: CreativeBrief, plan: PlanningArtifact) -> str:
    seconds = brief.target.duration_target_ms / 1000
    min_chars = round(seconds * 3.2)
    max_chars = round(seconds * 3.8)
    compact_plan = json.dumps(
        plan.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    return f"""基于已经批准的 CreativeBrief 和 Planned Beats 写 UGC 口播稿。

CreativeBrief:
{brief.model_dump_json(indent=2)}

PlanningArtifact:
{compact_plan}

硬性要求：
- 不得改变 Beat 顺序、ID、论点或新增没有规划的核心主张。
- 每个 Planned Beat 至少对应 1 个 ScriptSegment；通常一个 Beat 对应一个 Segment。
- 总口播正文控制在约 {min_chars}–{max_chars} 个可见字符，以适配约 {seconds:.0f} 秒。
- 前两句短、快、能口播；不要用“大家好，今天我们来聊聊”。
- 句子短，像真人对镜头说话，允许自然转折和轻微情绪，但不要油腻。
- 每 15–25 秒应有一次新问题、反转、证据或总结性重音。
- 遇到尚未提供来源的事实主张，用审慎措辞，不要编造具体数字或来源。
- Close 回扣 Hook，给明确 takeaway；CTA 只有自然适合时才出现。
- emphasis_words 必须来自对应 text。
- speech_act 必须与对应 Beat 的 discourse_role 一致。
- ID 使用 ss01、ss02……且唯一。

返回对象必须严格符合：
{{
  "script_version": "v1",
  "title_options": ["string", "string", "string"],
  "segments": [
    {{
      "script_segment_id": "ss01",
      "planned_beat_id": "pb01",
      "text": "string",
      "delivery_hint": {{
        "speech_act": "question",
        "emphasis_words": ["string"],
        "pause_before_ms": 0,
        "pause_after_ms": 180,
        "energy": "high"
      }}
    }}
  ]
}}
"""


def planning_quality_repair_prompt(
    brief: CreativeBrief,
    plan: PlanningArtifact,
    problem: str,
) -> str:
    return f"""{planning_prompt(brief)}

上一版 PlanningArtifact 虽然 JSON 结构正确，但没有通过本地内容结构质检：
{problem}

上一版：
{plan.model_dump_json(indent=2)}

请重新生成完整 PlanningArtifact。重点修正上述问题，并保持有效部分的叙事逻辑。
"""


def script_quality_repair_prompt(
    brief: CreativeBrief,
    plan: PlanningArtifact,
    script: ScriptArtifact,
    problem: str,
) -> str:
    seconds = brief.target.duration_target_ms / 1000
    target_chars = round(seconds * 3.55)
    return f"""{script_prompt(brief, plan)}

上一版 ScriptArtifact 虽然 JSON 结构正确，但没有通过本地成稿质检：
{problem}

上一版：
{script.model_dump_json(indent=2)}

请重新生成完整 ScriptArtifact：
- 正文目标约 {target_chars} 个可见字符；
- 优先压缩重复解释，不删除任何 Beat；
- 每个 Beat 仍须至少有一个 Segment；
- 所有 emphasis_words 必须原样出现在对应 text 中。
"""


def repair_prompt(
    original_prompt: str, invalid_json: str, validation_error: str
) -> str:
    return f"""{original_prompt}

你上一次的输出未通过结构校验。
校验错误：
{validation_error}

上一次输出：
{invalid_json}

请修复所有错误并重新返回完整 JSON。不要解释。
"""
