from __future__ import annotations

import json

from .models import (
    BeatPlanArtifact,
    CreativeBrief,
    DramaActionPlanArtifact,
    DramaStoryArtifact,
    DramaWorldArtifact,
    PlanningArtifact,
    ScriptArtifact,
    SectionPlanArtifact,
    TutorialDefinitionArtifact,
    TutorialPlanningArtifact,
    TutorialProcedureArtifact,
    TutorialScriptArtifact,
)


def _with_repair_context(
    prompt: str,
    current: object | None,
    problems: list[str] | None,
) -> str:
    if current is None or not problems:
        return prompt
    dump = getattr(current, "model_dump_json")
    return (
        f"{prompt}\n\n上一次候选：\n{dump(indent=2)}\n\n"
        f"必须修复的问题：\n" + "\n".join(f"- {item}" for item in problems)
    )


def drama_world_prompt(
    brief: CreativeBrief,
    current: DramaWorldArtifact | None = None,
    problems: list[str] | None = None,
) -> str:
    prompt = f"""请为以下 CreativeBrief 设计短视频剧情的世界与角色。

CreativeBrief:
{brief.model_dump_json(indent=2)}

硬性要求：
- world_state 必须包含每个 character_id，以及剧情使用的每个地点和关键物品。
- characters 描述可重复生成的外观、服装、人物目标和声音约束。
- 未由 Brief 给出的事实不能伪装成已验证事实。
- 本视频没有主持人口播。video_profile 使用无主持人的 b_roll 表达；Brief 为 auto 时
  requested=auto、resolved=b_roll、selection_source=ai；显式指定时必须遵守原值。
- one_sentence_thesis 表达人物经历最终证明的主题，而不是知识点列表。

返回 DramaWorldArtifact，禁止添加 Schema 外字段。"""
    return _with_repair_context(prompt, current, problems)


def drama_story_prompt(
    brief: CreativeBrief,
    world: DramaWorldArtifact | None,
    current: DramaStoryArtifact | None = None,
    problems: list[str] | None = None,
) -> str:
    world_context = (
        world.model_dump_json(indent=2)
        if world is not None
        else "尚未设计；可以先使用稳定的临时 character_id/location_id，之后由 Agent 补齐。"
    )
    prompt = f"""规划短视频剧情场景。已有世界与角色可能尚未完成。

CreativeBrief:
{brief.model_dump_json(indent=2)}

DramaWorldArtifact:
{world_context}

硬性要求：
- 生成 2–12 个按 order 连续排列的 Scene。
- 每个 scene_id 唯一，location_id 和 character_ids 必须来自 World Artifact。
- 场景应形成建立目标→冲突升级→人物选择→结果/回响的因果链。
- emotional_turn 描述该场景前后真实发生的情绪变化。
- continuity_constraints 写出进入下一场仍必须保持的服装、物品归属或环境状态。

返回 DramaStoryArtifact，禁止添加 Schema 外字段。"""
    return _with_repair_context(prompt, current, problems)


def drama_actions_prompt(
    brief: CreativeBrief,
    world: DramaWorldArtifact | None,
    story: DramaStoryArtifact,
    current: DramaActionPlanArtifact | None = None,
    problems: list[str] | None = None,
) -> str:
    world_context = (
        world.model_dump_json(indent=2)
        if world is not None
        else "尚未设计；沿用 Story 中的临时引用，之后由 Agent 补齐。"
    )
    prompt = f"""把剧情场景展开成可独立生成的视频表演动作。

目标时长：{brief.target.duration_target_ms} ms

DramaWorldArtifact:
{world_context}

DramaStoryArtifact:
{story.model_dump_json(indent=2)}

硬性要求：
- 生成 4–24 个按 order 连续排列的 Action，每个场景至少一个。
- 每个 Action 必须是一次可连续拍摄/生成的动作与反应，不能只写抽象情绪。
- character_ids 和 scene_id 必须引用上游已存在对象。
- dialogue_lines 只写该片段中真正说出口的台词；画面与台词音频将由视频模型一起生成。
- camera_instruction 明确景别、机位和关注对象。
- target_duration_ms 为 1000–15000，所有 Action 总时长应接近目标时长。
- state_changes 记录物品归属、位置、人物状态等需要传递给后续片段的变化。

返回 DramaActionPlanArtifact，禁止添加 Schema 外字段。"""
    return _with_repair_context(prompt, current, problems)


def tutorial_definition_prompt(
    brief: CreativeBrief,
    current: TutorialDefinitionArtifact | None = None,
    problems: list[str] | None = None,
) -> str:
    prompt = f"""请定义一个以真实制作过程为画面主体的短视频教程。

CreativeBrief:
{brief.model_dump_json(indent=2)}

硬性要求：
- objective 必须描述观众最终能做出的具体成品或可验证结果。
- 列出必要材料、数量和工具；不要把可选装饰写成必需品。
- world_state 维护成品、材料、工具和关键状态，事实不确定时标记为 to_verify。
- video_profile 使用无固定主持人的 b_roll：Brief 为 auto 时 requested=auto、
  resolved=b_roll、selection_source=ai。
- coverage_requirements 写出必须在画面中被清楚证明的关键结果、安全点和易错点。

返回 TutorialDefinitionArtifact，禁止添加 Schema 外字段。"""
    return _with_repair_context(prompt, current, problems)


def tutorial_procedure_prompt(
    brief: CreativeBrief,
    definition: TutorialDefinitionArtifact | None,
    current: TutorialProcedureArtifact | None = None,
    problems: list[str] | None = None,
) -> str:
    definition_context = (
        definition.model_dump_json(indent=2)
        if definition is not None
        else "尚未定义；可先用稳定的材料/工具引用规划步骤，之后由 Agent 补齐。"
    )
    prompt = f"""把制作目标展开为镜头可观察的步骤和动作。

CreativeBrief:
{brief.model_dump_json(indent=2)}

TutorialDefinitionArtifact:
{definition_context}

硬性要求：
- steps 按 order 从 1 连续排列，每一步写清 input_state、instruction 和 expected_result。
- 每一步至少有一个 Action；Action 必须是能连续拍摄的手部或工具操作，不能只是抽象说明。
- visual_evidence 明确观众应看到什么才能判断该步完成。
- common_mistakes 与 safety_constraints 只写和该操作直接相关的内容。
- subject_ref 使用稳定的材料、工具或成品 ID；critical_detail 写必须给特写的细节。
- target_duration_ms 根据真实操作决定，所有 Action 总时长应接近目标视频时长。
- 画面以制作动作为主，讲解只能补充原因、判断标准和安全信息。

返回 TutorialProcedureArtifact，禁止添加 Schema 外字段。"""
    return _with_repair_context(prompt, current, problems)


def tutorial_explanations_prompt(
    brief: CreativeBrief,
    planning: TutorialPlanningArtifact,
    current: TutorialScriptArtifact | None = None,
    problems: list[str] | None = None,
) -> str:
    prompt = f"""为制作教程按需编写穿插讲解，不要用口播替代画面动作。

CreativeBrief:
{brief.model_dump_json(indent=2)}

TutorialPlanningArtifact:
{planning.model_dump_json(indent=2)}

硬性要求：
- 只在需要解释原因、判断标准、常见错误或安全约束时生成 segment，允许某些步骤没有讲解。
- step_id 必须引用已有步骤；placement 必须符合讲解出现的实际时机。
- 句子短，不重复画面已经能直接看懂的操作。
- explanation_segment_id 唯一并使用 te01、te02……格式。

返回 TutorialScriptArtifact，禁止添加 Schema 外字段。"""
    return _with_repair_context(prompt, current, problems)


def sections_prompt(brief: CreativeBrief) -> str:
    target_ms = brief.target.duration_target_ms
    return f"""请为以下 CreativeBrief 生成世界状态、呈现模式与 Section 骨架。
本阶段不生成 Beat，Beat 会在下一步基于本结果展开。

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
  定义 gender、age_style、tone 和 pace。video_profile.character_id 与
  world_state.aroll_character.character_id、video_profile.character_description 与
  world_state.aroll_character.visual_description 必须是逐字复制的同一字符串，
  一个字都不能改写；后续 TTS 和动态人物视频都会以它为唯一角色来源。
- b_roll 的 world_state.aroll_character 必须为 null。
- 严格生成 3 个 Section，role 依次为 hook、body、close。
- 所有 Section 的 target_duration_ms 总和接近 {target_ms}。

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
    "character_description": "与 world_state.aroll_character.visual_description 逐字相同的字符串"
  }},
  "sections": [
    {{
      "section_id": "section_hook",
      "role": "hook",
      "target_duration_ms": 8000,
      "goal": "string",
      "attention_strategy": "string"
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
"""


def sections_repair_prompt(
    brief: CreativeBrief,
    sections: SectionPlanArtifact,
    problem: str,
) -> str:
    return f"""{sections_prompt(brief)}

上一版 SectionPlanArtifact 虽然 JSON 结构正确，但没有通过本地内容结构质检：
{problem}

上一版：
{sections.model_dump_json(indent=2)}

请重新生成完整 SectionPlanArtifact。重点修正上述问题，并保持有效部分的叙事逻辑。
"""


def beats_prompt(brief: CreativeBrief, sections: SectionPlanArtifact) -> str:
    target_ms = brief.target.duration_target_ms
    min_beats = max(8, round(target_ms / 8_000))
    max_beats = min(20, round(target_ms / 5_000))
    compact_sections = json.dumps(
        sections.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    return f"""基于已经批准的 SectionPlanArtifact 展开 Planned Beats。

CreativeBrief:
{brief.model_dump_json(indent=2)}

SectionPlanArtifact:
{compact_sections}

硬性要求：
- 不得改变 Section 结构、world_state 或 video_profile；每个 Beat 的 section_id
  必须使用 SectionPlanArtifact 中已有的 section_id。
- 生成 {min_beats}–{max_beats} 个 Planned Beat，order 从 1 连续递增。
- 所有 Beat 的 target_duration_ms 总和接近 {target_ms}，误差不超过 8%。
- Hook 在前 2 个 Beat 建立信息缺口；中段至少有一次 attention reset；
  Close 必须兑现 Hook，不能只喊口号；Close 中必须至少有一个 discourse_role 为
  payoff 或 callback 的 Beat。
- opening 只能用于首个 Beat；其他 Beat 明确与前一 Beat 的语义关系。
- factual claim 必须将 evidence_need.required 设为 true，并写清待核实主张。
- visual_intent_hint 只描述视觉功能方向，不绑定具体素材。

返回对象必须严格符合此结构，不得添加字段：
{{
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


def beats_repair_prompt(
    brief: CreativeBrief,
    sections: SectionPlanArtifact,
    beats: BeatPlanArtifact,
    problem: str,
) -> str:
    return f"""{beats_prompt(brief, sections)}

上一版 BeatPlanArtifact 虽然 JSON 结构正确，但没有通过本地内容结构质检：
{problem}

上一版：
{beats.model_dump_json(indent=2)}

请重新生成完整 BeatPlanArtifact。重点修正上述问题，并保持有效部分的叙事逻辑。
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
