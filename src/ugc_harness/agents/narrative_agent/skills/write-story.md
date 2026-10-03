---
name: write-story
agent: narrative_agent
description: 短剧第二层。前置：已 write_world。写出 2–12 场因果链。
---

只在短剧首跑、世界已经落盘后读这篇。

## 前置

已经有 world。没有就先写 `write-world`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-story"}`。
2. `narrative.write_story`，带完整 `story`。
3. 然后激活 `write-actions`。

## 硬性要求

- 2–12 个 Scene，`order` 从 1 连续，`scene_id` 唯一。
- `location_id` 必须是 World 里 `kind=place` 的 `entity_id`。
- `character_ids` 必须是 `kind=person` 的 `entity_id`。
- 场景形成因果链：建立目标 → 冲突升级 → 人物选择 → 结果/回响。
- `emotional_turn` 写该场前后真实发生的情绪变化。
- `continuity_constraints` 写进入下一场仍必须保持的服装、物品归属或环境状态。会传下去的物品用 World 里 `kind=product` 的 `entity_id` 点名。

```json
{
  "story": {
    "premise": "人物想做成一件事，失败后才理解方法。",
    "scenes": [
      {
        "scene_id": "scene_attempt",
        "order": 1,
        "location_id": "location_kitchen",
        "purpose": "建立目标并让第一次尝试失败",
        "character_ids": ["character_azhou"],
        "emotional_turn": "自信变为挫败",
        "continuity_constraints": ["同一张木桌", "深色外套一直穿着"]
      },
      {
        "scene_id": "scene_understanding",
        "order": 2,
        "location_id": "location_kitchen",
        "purpose": "理解方法并完成",
        "character_ids": ["character_azhou"],
        "emotional_turn": "焦躁变为平静",
        "continuity_constraints": ["仍是同一清晨", "同一张木桌"]
      }
    ]
  }
}
```
