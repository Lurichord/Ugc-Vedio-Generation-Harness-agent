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
3. factual 只用于识别口播中的事实性陈述和辅助素材搜索；本阶段不生成证据请求，
   不判断事实是否成立，也不因为缺少来源而修改剧本。
   一般性因果判断、趋势、预测、数量级比较和机构行为仍应正确分类。
4. interpretation 必须 interpretation_label_required=true。不要把事实为了省事归类成观点。
5. 不得声称任何事实已经过验证。
6. 为每个 RealizedBeat 严格生成一个 VisualRequirement。
7. 每个 VisualRequirement 的 selection_policy 固定为 first_success。
8. directions 是按 order 排列的探索方向，不是 top-k 候选。执行阶段从 order=1 开始；
   某个方向一旦找到一份满足要求的素材就立即停止，不再执行后续方向。
9. 每个方向只描述一种容易执行的素材方向，不要要求一个素材同时展示多个公司、项目或事件。
   covers_claim_ids 可以为空，也可以只覆盖当前方向实际能表达的一条或少量主张。
10. 画面不需要全部是证据。方向可以承担 illustration、context、explanation、emotion、
    contrast、humor、identity、reset、bridge 或 reconstruction。
11. visual_role=evidence 时必须引用 factual claim、grounding_requirement=source_exact，
    asset_type 只能使用真实来源类型，禁止 ai_image 和 ai_video。
12. AI 图片/视频只能承担非证据功能，必须 generated_media_disclosure_required=true，
    并写清 must_not_imply。
13. query 是将来交给搜索、生成或渲染工具的一条短指令，不是假来源。
14. 不能因为暂时没有画面就删除主张。
15. 当前 MVP 不进行联网真实视频检索，asset_type 禁止使用 real_video。事实与现实场景优先使用
    来源截图、文件截图、图表或真实图片；动态表达使用屏幕录制、动态图形、静态图动画或 AI 说明视频。

只返回严格符合下面结构的 JSON，不要 Markdown，不得增加字段：
{{
  "plan_version": "v3",
  "project_id": "{stage_one.brief.project_id}",
  "claims": [
    {{
      "claim_id": "c01",
      "beat_id": "b01",
      "script_segment_ids": ["ss01"],
      "statement": "完整、可核实或可识别的主张",
      "claim_type": "factual",
      "importance": 0.8,
      "interpretation_label_required": false
    }}
  ],
  "visual_requirements": [
    {{
      "visual_request_id": "vr01",
      "beat_id": "b01",
      "purpose": "这个 Beat 最低限度需要完成的视觉作用",
      "selection_policy": "first_success",
      "directions": [
        {{
          "direction_id": "vr01_d01",
          "order": 1,
          "description": "一个容易检索、可以独立满足视觉目的的方向",
          "visual_role": "context",
          "asset_type": "real_image",
          "query": "一条短图片检索词",
          "covers_claim_ids": [],
          "grounding_requirement": "contextual",
          "generated_media_disclosure_required": false,
          "must_not_imply": []
        }},
        {{
          "direction_id": "vr01_d02",
          "order": 2,
          "description": "仅在第一个方向失败后尝试的 AI 说明方向",
          "visual_role": "illustration",
          "asset_type": "ai_video",
          "query": "一条简洁的 AI 说明画面生成指令",
          "covers_claim_ids": [],
          "grounding_requirement": "contextual",
          "generated_media_disclosure_required": true,
          "must_not_imply": ["AI 画面不是新闻现场或真实记录"]
        }}
      ]
    }}
  ]
}}

枚举：
- claim_type: factual, interpretation, opinion, rhetorical
- visual role: evidence, illustration, context, explanation, emotion, contrast,
  humor, identity, reset, bridge, reconstruction
- modality: source_screenshot, document_screenshot, chart, real_image,
  meme, kinetic_typography, motion_graphic, ai_image, ai_video, screen_recording,
  talking_head
- grounding_requirement: none, contextual, source_exact
- selection_policy: first_success
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
