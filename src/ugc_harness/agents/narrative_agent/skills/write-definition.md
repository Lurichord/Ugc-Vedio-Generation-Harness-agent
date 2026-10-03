---
name: write-definition
agent: narrative_agent
description: 教程第一层。没有前置。写出成品、材料身份册和必须看清的证据点。
---

只在教程首跑第一层读这篇。激活不等于落盘。

## 前置

无。若叙事产物里已经有成品定义，不要重写，去 `write-procedure`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-definition"}`。
2. `narrative.write_definition`，带完整 `definition`。工具同时写入材料身份册。
3. 然后激活 `write-procedure`。

## 身份册

登记成品和反复上手的材料/工具。不要为每颗螺丝建条。

- `materials` 与 `kind=product` 的 entity **使用同一套 id**。同一件东西只登记一次，不要既写 `materials` 又另开一条同名 entity。
- 路人、一次性名词不要进册。
- 物、地的 `description` 各写 **2–3 个跨镜不变的静态特征**（形状 / 材料 / 颜色，或空间结构 + 主光源），供出片时 `定义为…`。不要写「处理好的材料」这种空话。

## 硬性要求

- `objective` 必须是观众最终能做出的具体成品或可验证结果。
- 列出必要材料、数量和工具；不要把可选装饰写成必需品。
- `world_state` 维护成品、材料、工具和关键状态。事实不确定标 `to_verify` 且 `evidence_required=true`。
- `video_profile` 用无固定主持人的 `b_roll`。Brief 为 `auto` 时 `requested=auto`、`resolved=b_roll`、`selection_source=ai`。
- `coverage_requirements` 写出必须在画面中被清楚证明的关键结果、安全点和易错点。
- `one_sentence_thesis` 跟 Brief。

```json
{
  "definition": {
    "one_sentence_thesis": "用粽叶折出严密漏斗并控制米量，就能包出不漏米的四角粽",
    "world_state": {
      "topic_frame": "家庭厨房里的包粽子",
      "entities": [
        {
          "entity_id": "mat_leaf",
          "name": "粽叶",
          "kind": "product",
          "narrative_role": "material",
          "description": "深绿宽叶、叶脉清晰、叶片完整"
        }
      ],
      "claims": [
        {
          "claim_id": "wc01",
          "statement": "底部闭合才不漏米",
          "epistemic_status": "given_by_brief",
          "evidence_required": false
        }
      ]
    },
    "video_profile": {
      "requested": "auto",
      "resolved": "b_roll",
      "selection_source": "ai",
      "rationale": "教程以手部制作为画面主体",
      "speaker_presence_ratio_min": 0.0,
      "speaker_presence_ratio_max": 0.15,
      "character_consistency_required": false,
      "character_id": null,
      "character_description": null
    },
    "objective": "完成一个包裹紧实、四角清楚且不漏米的传统粽子",
    "materials": [{"material_id": "mat_leaf", "name": "处理好的粽叶", "quantity": "2片"}],
    "tools": ["剪刀", "勺子"],
    "coverage_requirements": ["漏斗底部无孔", "米量不过满", "棉线固定不松散"]
  }
}
```
