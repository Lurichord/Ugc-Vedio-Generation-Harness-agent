---
name: write-world
agent: narrative_agent
description: 短剧第一层。没有前置。写出世界、身份册和角色。
---

只在短剧首跑第一层读这篇。激活不等于落盘。

只改人设、不改镜头时，用 `narrative.write_identities` 交完整身份册，写法与下面同一套。

## 前置

无。若叙事产物里已经有世界和身份册，不要重写，去 `write-story`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-world"}`。
2. `narrative.write_world`，带完整 `world`。工具同时写入身份册。
3. 然后激活 `write-story`。

## 身份册

只登记开口角色、场次地点、会转手或反复出现的道具。群众和桌上闲杂物不进。

- 人 `person`、地 `place`、物 `product`；`entity_id` 稳定。后续 scene / action 只能引用这些 id。
- 台词里闪过的名词、路人、一次性画面不要进册。
- `world_state` 必须包含每个 `character_id`，以及剧情使用的每个地点和关键物品。
- 每个 `characters.character_id` 必须同时是 `kind=person` 的 `entity_id`。
- 人、地、物的 `description` 各写 **2–3 个跨镜不变的静态特征**，供出片时 `定义为…`。不要写情绪、剧情或「可重复生成的外观」这种空话。
  - 人：发型、服装主色或款式、体态。
  - 地：空间结构 + 主光源（再加一块反复出现的陈设）。
  - 物：形状、材料、颜色。
- `characters` 的 `description` 与对应 person entity **同一套静态特征**。`appearance_constraints` 只写锁得住的东西（始终同一件旧棉衣），不写情绪。`voice_constraints` 写音色：年龄、性别、音域、语气，不要只写「对白自然」。

## 其它硬性要求

- `one_sentence_thesis` 表达人物经历最终证明的主题，不是知识点列表。跟 Brief。
- 未由 Brief 给出的事实不能伪装成已验证事实。主张不确定标 `to_verify` 且 `evidence_required=true`。
- 本视频没有主持人口播。`video_profile` 用无主持人的 `b_roll`。Brief 为 `auto` 时 `requested=auto`、`resolved=b_roll`、`selection_source=ai`；Brief 已指定则必须遵守原值。

```json
{
  "world": {
    "one_sentence_thesis": "人物经历最终证明的主题",
    "world_state": {
      "topic_frame": "本片世界怎么框",
      "entities": [
        {
          "entity_id": "character_azhou",
          "name": "阿舟",
          "kind": "person",
          "narrative_role": "protagonist",
          "description": "短发、深色旧棉衣、中等体态"
        },
        {
          "entity_id": "location_kitchen",
          "name": "老厨房",
          "kind": "place",
          "narrative_role": "primary_location",
          "description": "旧木桌靠窗、下午侧光、墙边冰箱"
        },
        {
          "entity_id": "prop_bowl",
          "name": "青花碗",
          "kind": "product",
          "narrative_role": "key_prop",
          "description": "圆口、白底青花、中等大小"
        }
      ],
      "claims": [
        {
          "claim_id": "wc01",
          "statement": "Brief 里已经给出的事实",
          "epistemic_status": "given_by_brief",
          "evidence_required": false
        }
      ]
    },
    "video_profile": {
      "requested": "auto",
      "resolved": "b_roll",
      "selection_source": "ai",
      "rationale": "短剧没有主持人口播",
      "speaker_presence_ratio_min": 0.0,
      "speaker_presence_ratio_max": 0.15,
      "character_consistency_required": false,
      "character_id": null,
      "character_description": null
    },
    "characters": [
      {
        "character_id": "character_azhou",
        "name": "阿舟",
        "description": "短发、深色旧棉衣、中等体态",
        "dramatic_objective": "人物要做成的事",
        "appearance_constraints": ["始终同一件深色旧棉衣"],
        "voice_constraints": ["中年男声", "中低音域", "语气发闷克制"]
      }
    ]
  }
}
```
