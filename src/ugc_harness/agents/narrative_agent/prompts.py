from __future__ import annotations

import json

from .models import CreativeBrief, PlanningArtifact, ScriptArtifact


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
- 先建立本视频的 world_state：明确涉及的实体、会在视频里使用的主张、因果关系、
  待回答问题和不能越过的叙事边界。它描述视频内容世界，不描述模型或工具环境。
- 未经 CreativeBrief 直接提供、但会按事实讲述的主张，epistemic_status 必须为
  to_verify，且 evidence_required 必须为 true；观点和推断不能伪装成已知事实。
- 生成 video_profile 决策。CreativeBrief.video_profile 不是 auto 时必须原样采用，
  selection_source=user；为 auto 时由你在 a_roll、b_roll、ab_roll 中选择，
  selection_source=ai。a_roll 是人物口播画面，b_roll 是画外音配补充画面，
  ab_roll 是一致人物口播为主、按需插入证据/梗图/AI 补充画面。
- a_roll/ab_roll 必须定义稳定的 character_id 和 character_description；ab_roll 的
  人物出镜占比建议为 0.55–0.75。
- a_roll/ab_roll 还必须在 world_state.aroll_character 中保存同一个人物，并结构化
  定义 gender、age_style、tone 和 pace。video_profile.character_id/character_description
  必须与 world_state 中完全一致，后续 TTS 和动态人物视频都会以它为唯一角色来源。
- b_roll 的 world_state.aroll_character 必须为 null。
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
  "world_state": {{
    "topic_frame": "本视频如何界定和理解主题",
    "entities": [
      {{
        "entity_id": "entity_01",
        "name": "string",
        "kind": "concept",
        "narrative_role": "string",
        "description": "string"
      }}
    ],
    "claims": [
      {{
        "claim_id": "wc01",
        "statement": "string",
        "epistemic_status": "to_verify",
        "evidence_required": true
      }}
    ],
    "causal_links": [
      {{"cause": "string", "effect": "string", "explanation": "string"}}
    ],
    "open_questions": ["string"],
    "narrative_boundaries": ["string"],
    "aroll_character": {{
      "character_id": "host_main",
      "visual_description": "固定人物的年龄、性别呈现、发型、服装、气质和拍摄环境",
      "voice_profile": {{
        "gender": "male",
        "age_style": "young",
        "tone": "温和、亲切、知识型",
        "pace": "natural"
      }}
    }}
  }},
  "video_profile": {{
    "requested": "auto",
    "resolved": "ab_roll",
    "selection_source": "ai",
    "rationale": "string",
    "speaker_presence_ratio_min": 0.55,
    "speaker_presence_ratio_max": 0.75,
    "character_consistency_required": true,
    "character_id": "host_main",
    "character_description": "固定人物的年龄、发型、服装、气质和拍摄环境"
  }},
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
- entity.kind: person, organization, place, product, concept, event, other
- claim.epistemic_status: given_by_brief, to_verify, interpretation, hypothesis
- arroll_character.voice_profile.gender: male, female, neutral
- arroll_character.voice_profile.age_style: young, mature, senior
- arroll_character.voice_profile.pace: slow, natural, fast
- video_profile.requested: auto, a_roll, b_roll, ab_roll
- video_profile.resolved: a_roll, b_roll, ab_roll
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
- 必须服从 PlanningArtifact.world_state；不得把 to_verify、interpretation 或
  hypothesis 改写成无条件成立的已知事实。
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
