from __future__ import annotations

import json

from ..stage_one.models import StageOneArtifact
from ..stage_two.models import VoiceStageArtifact


def editorial_plan_prompt(
    stage_one: StageOneArtifact,
    stage_two: VoiceStageArtifact,
) -> str:
    beats = [
        {
            "beat_id": beat.beat_id,
            "planned_beat_id": beat.planned_beat_id,
            "start_ms": beat.start_ms,
            "end_ms": beat.end_ms,
            "narration": beat.narration,
            "proposition": beat.proposition,
            "discourse_role": beat.discourse_role,
            "relation_to_previous": beat.relation_to_previous,
            "script_segment_ids": beat.script_segment_ids,
        }
        for beat in stage_two.realized_beats
    ]
    context = {
        "brief": stage_one.brief.model_dump(mode="json"),
        "one_sentence_thesis": stage_one.planning.one_sentence_thesis,
        "realized_beats": beats,
    }
    return f"""你是 UGC 视频的事实编辑和视觉需求规划器。
请分析下面已经生成并完成配音的口播，不要重写剧本，不要搜索或编造来源。

输入：
{json.dumps(context, ensure_ascii=False, indent=2)}

任务：
1. 从口播中抽取真正需要管理的主张。一个 Beat 可以没有主张，也可以有多个主张。
2. 将主张分类为 factual、interpretation、opinion 或 rhetorical。
3. factual 必须 evidence_required=true、source_status=research_required，并生成对应 EvidenceRequest。
   输入中第一阶段已经标记 evidence_need.required 的信息会体现在 proposition 和 narration 中；
   这些 Beat 必须至少抽取一个 factual，不能漏掉或为了减少检索而降成 interpretation。
   一般性因果判断、趋势、预测、数量级比较、机构行为和“会导致”等可检验陈述也属于 factual。
4. interpretation 必须 interpretation_label_required=true。不要把事实为了省事归类成观点。
5. 此阶段尚未检索来源，不得声称任何事实已验证。
6. 为每个 RealizedBeat 严格生成一个 VisualRequirement。
7. 画面不需要全部是证据。可以承担 illustration、context、explanation、emotion、
   contrast、humor、identity、reset、bridge 或 reconstruction。
8. 当事实需要来源但没有真实视觉素材时，允许用通用真实素材、动态图形或 AI 生成画面进行解释；
   这些画面只能是说明性画面，generated_media_can_satisfy_evidence 永远为 false。
9. primary_role=evidence 时必须引用 factual claim，grounding_requirement=source_exact，
   优先 source_screenshot、document_screenshot、chart、real_video 或 real_image。
   preferred_modalities 中禁止 ai_image 和 ai_video。若真实证据画面获取失败，可以把 AI
   说明画面写入 fallback_ladder，但届时它只承担 illustration，不能继续标为 evidence。
10. AI 图片/视频必须 generated_media_allowed=true、
    generated_media_disclosure_required=true，并写清 must_not_imply。
11. fallback_ladder 描述素材获取失败后的视觉降级顺序。不能因为暂时没有画面就删除主张。
12. search_queries 是将来交给 Web Search MCP 的短查询，不是假来源。

只返回严格符合下面结构的 JSON，不要 Markdown，不得增加字段：
{{
  "plan_version": "v1",
  "project_id": "{stage_one.brief.project_id}",
  "claims": [
    {{
      "claim_id": "c01",
      "beat_id": "b01",
      "script_segment_ids": ["ss01"],
      "statement": "完整、可核实或可识别的主张",
      "claim_type": "factual",
      "importance": 0.8,
      "evidence_required": true,
      "interpretation_label_required": false,
      "source_status": "research_required",
      "if_unsupported": "modify_or_remove"
    }}
  ],
  "evidence_requests": [
    {{
      "evidence_request_id": "er01",
      "claim_id": "c01",
      "claim_to_verify": "需要验证的精确事实",
      "search_queries": ["查询一", "查询二"],
      "acceptable_source_types": ["official_document", "reputable_news"],
      "preferred_publishers": [],
      "verification_questions": ["来源是否直接支持该主张？"],
      "visual_evidence_desired": true,
      "direct_visual_evidence_required": false
    }}
  ],
  "visual_requirements": [
    {{
      "visual_request_id": "vr01",
      "beat_id": "b01",
      "primary_role": "illustration",
      "supporting_roles": ["context"],
      "purpose": "该画面对观众承担的功能",
      "content_description": "需要呈现的视觉内容",
      "preferred_modalities": ["real_video", "ai_video"],
      "search_queries": ["可选素材查询"],
      "evidence_claim_ids": [],
      "grounding_requirement": "contextual",
      "generated_media_allowed": true,
      "generated_media_can_satisfy_evidence": false,
      "generated_media_disclosure_required": true,
      "must_not_imply": ["AI 画面不是新闻现场或真实记录"],
      "fallback_ladder": [
        "相关真实素材",
        "通用 B-roll",
        "AI 说明性画面",
        "口播加动态字幕"
      ],
      "max_asset_count": 2
    }}
  ]
}}

枚举：
- claim_type: factual, interpretation, opinion, rhetorical
- source_status: research_required, not_applicable
- if_unsupported: retain_as_opinion, reframe_as_interpretation, modify_or_remove
- visual role: evidence, illustration, context, explanation, emotion, contrast,
  humor, identity, reset, bridge, reconstruction
- modality: source_screenshot, document_screenshot, chart, real_video, real_image,
  meme, kinetic_typography, motion_graphic, ai_image, ai_video, talking_head
- grounding_requirement: none, contextual, source_exact
"""


def editorial_repair_prompt(
    stage_one: StageOneArtifact,
    stage_two: VoiceStageArtifact,
    invalid_plan_json: str,
    problems: list[str],
) -> str:
    return f"""{editorial_plan_prompt(stage_one, stage_two)}

上一版虽然通过 JSON 结构校验，但没有通过本地编辑规则：
{json.dumps(problems, ensure_ascii=False)}

上一版：
{invalid_plan_json}

请返回修复后的完整对象。保留正确判断，并修复所有覆盖率、引用和媒体角色问题。
"""
