from __future__ import annotations

import json

from ..narrative_agent.models import NarrativeArtifact
from ..voice_agent.models import VoiceArtifact


def editorial_plan_prompt(
    narrative: NarrativeArtifact,
    voice: VoiceArtifact,
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
        for beat in voice.realized_beats
    ]
    context = {
        "brief": narrative.brief.model_dump(mode="json"),
        "world_state": narrative.planning.world_state.model_dump(mode="json"),
        "video_profile": narrative.planning.video_profile.model_dump(mode="json"),
        "one_sentence_thesis": narrative.planning.one_sentence_thesis,
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
9. 每个方向只描述一种下游真能执行的素材。对比/差异可以：检索两侧实景、检索一张
   来源图表，或 AI 静帧画两侧真实景物 / 把口播已有数字画成简洁图。不要写成
   「南北物产差异对比说明动画」，也不要两个 Logo/图标代表两边。
   covers_claim_ids 可以为空，也可以只覆盖当前方向实际能表达的一条或少量主张。
10. 画面不需要全部是证据。方向可以承担 illustration、context、explanation、emotion、
    contrast、humor、identity、reset、bridge 或 reconstruction。
11. visual_role=evidence 时必须引用 factual claim、grounding_requirement=source_exact，
    asset_type 只能使用真实来源类型（优先 chart、source_screenshot、
    document_screenshot、real_image），禁止 ai_image 和 ai_video。证据只能放在
    最后一条 AI 兜底之前。
12. AI 画面只能承担非证据功能，必须 generated_media_disclosure_required=true，
    并写清 must_not_imply。有出处时允许 AI 画图表：数字和对比项必须来自本 Beat
    口播或对应 factual claim，不得发明数据，不得伪造官网、台标、水印。
13. query 按 asset_type 分流，不是假来源、也不是分镜剧本：
    - 检索类（real_image/chart/截图/meme）：写成能搜到的中文关键词，偏向实景照片
      或带数据的来源页，避开品牌官网 Logo、App 图标、百科信息框小标。
    - ai_image：写成 9:16 竖屏静帧生成提示，点名具体景物、构图；若是图表，只写
      轴上要出现的短数字/类目，不要抄整句口播。画面无字幕、无字幕条、无大字标题。
      不要写「动画」「动态对比」「生成一段视频」。字幕由成片后期叠，不要画进图。
14. 不能因为暂时没有画面就删除主张。
15. 当前 MVP 不进行联网真实视频检索，asset_type 禁止使用 real_video。科普 B-roll
    先检索截图/图表/真实图片；失败后再用 ai_image 静帧。不要用 ai_video、
    motion_graphic、kinetic_typography 做解释或对比。时间线稍后可能轻微推拉静帧，
    不要在 editorial 里点名「说明动画」。
16. 必须原样复制输入的 video_profile。a_roll 表示人物口播主画面，b_roll 表示补充画面。
17. ab_roll 模式必须以 a_roll 为主：Hook、Close 和普通解释 Beat 优先人物口播；
    只有事实证据、例子、反差或注意力重置才切 b_roll，并满足 speaker_presence_ratio 范围。
18. a_roll 的 visual_role 使用 host_delivery，asset_type 使用 talking_head，speaker_visible=true，
    character_id 必须始终等于 video_profile.character_id，保证人物前后一致。
19. b_roll 中 factual/evidence 优先 source_screenshot、document_screenshot、chart 或 real_image；
    非证据说明用 illustration/explanation 的检索实景，最后一条用 ai_image。
    整帧禁止单一 Logo 或概念图标拼贴。
20. 每条 VisualRequirement 的最后一个 direction 必须是 AI 兜底：科普 B-roll 用
    ai_image（不要 ai_video），visual_role 不能是 evidence，generated_media_disclosure_required
    必须为 true，并写清 must_not_imply。前面的方向可以是网页实拍、来源图表、截图、
    证据或 talking_head；只有它们全部失败时才执行这条兜底。不允许出现「两条
    real_image、没有生成方向」这种清单。

只返回严格符合下面结构的 JSON，不要 Markdown，不得增加字段：
{{
  "plan_version": "v3",
  "project_id": "{narrative.brief.project_id}",
  "video_profile": {json.dumps(narrative.planning.video_profile.model_dump(mode="json"), ensure_ascii=False)},
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
      "track": "a_roll",
      "speaker_visible": true,
      "character_id": "host_main",
      "selection_policy": "first_success",
      "directions": [
        {{
          "direction_id": "vr01_d01",
          "order": 1,
          "description": "一个容易检索、可以独立满足视觉目的的方向",
          "visual_role": "host_delivery",
          "asset_type": "talking_head",
          "query": "固定人物口播画面",
          "covers_claim_ids": [],
          "grounding_requirement": "contextual",
          "generated_media_disclosure_required": false,
          "must_not_imply": []
        }},
        {{
          "direction_id": "vr01_d02",
          "order": 2,
          "description": "检索失败后的 9:16 实景静帧，不要动画、不要 Logo",
          "visual_role": "illustration",
          "asset_type": "ai_image",
          "query": "画面无任何字幕。9:16竖屏静帧，华北麦田金黄麦穗近景与江南水稻田水面秧苗分上下构图，真实景物，不要图标、不要Logo、不要字幕条、不要说明动画",
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
  humor, identity, reset, bridge, reconstruction, host_delivery
- modality: source_screenshot, document_screenshot, chart, real_image,
  meme, kinetic_typography, motion_graphic, ai_image, ai_video, screen_recording,
  talking_head
- grounding_requirement: none, contextual, source_exact
- selection_policy: first_success
"""


def editorial_repair_prompt(
    narrative: NarrativeArtifact,
    voice: VoiceArtifact,
    invalid_plan_json: str,
    problems: list[str],
) -> str:
    return f"""{editorial_plan_prompt(narrative, voice)}

上一版虽然通过 JSON 结构校验，但没有通过本地编辑规则：
{json.dumps(problems, ensure_ascii=False)}

上一版：
{invalid_plan_json}

请返回修复后的完整对象。保留正确判断，并修复所有覆盖率、引用和媒体角色问题。
不要用 ai_video、说明动画、Logo 或概念图标拼贴来「补画面」。科普 B-roll 兜底
仍用 ai_image 静帧；有出处的数字可以画成简洁图表，但不能当 evidence。
上面的本地规则问题是硬约束，不能原样保留失败值。若问题涉及人物出镜比例，必须使用
每个 RealizedBeat 的 end_ms-start_ms 计算时长加权比例，而不是按 Beat 数量计算；将足够
时长的普通解释 Beat 改为 a_roll，并同步设置 track=a_roll、speaker_visible=true、
character_id=video_profile.character_id，以及 host_delivery/talking_head 方向。返回前重新计算，
确保比例落在 video_profile 的最小值和最大值之间。
"""
