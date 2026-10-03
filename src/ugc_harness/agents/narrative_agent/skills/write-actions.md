---
name: write-actions
agent: narrative_agent
description: 短剧第三层。前置：已 write_story。把每场拆成可独立生成的表演动作。
---

只在短剧首跑、场次已经落盘后读这篇。

这里写的是导演卡，不是发给 Seedance 的全文。下游出片会按图序编译。不要在字段里写 `图片N`、`{}`、`<>`。

## 前置

已经有 story。没有就先写 `write-story`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-actions"}`。
2. `narrative.write_actions`，带完整 `actions`。
3. 然后激活 `write-shots`。

## 硬性要求

- 4–24 个 Action，`order` 从 1 连续。每个场景至少一个。
- 每个 Action 必须是**一次可连续拍摄/生成**的动作与反应，不能只写抽象情绪，也不能把两个景别塞进同一条。
- `description`：主体 + 一个主事件 + 可见反应。写到手、头、肩；带上速度和幅度。优先低缓连续小动作，不要狂奔、大跳、剧烈翻滚。情绪外化成身体，不写「很悲伤 / 非常愤怒」。镜内有先后用「先…随即…片刻后…」，不要写 `0–3秒`。
- `reaction` 同样写看得见的身体变化，不要只写心情。
- `character_ids` 和 `scene_id` 必须引用上游已有对象。
- 出镜物品只能点 World 里已有的 `product` `entity_id`，不要临编无名道具。
- `dialogue_lines` 只写该片段真正说出口的台词；一镜最多 1–2 句。不要外部旁白。
- `camera_instruction`：**一种景别 + 一种运镜**。只有「中景」就写机位固定。不要写「近景切到中景」，那种拆成两条 Action。
- `ambient_audio` 写成绑在动作上的具体声响，用顿号或逗号隔开，例如「门轴轻响、冰箱低鸣」。不要只写「厨房环境声」。
- `target_duration_ms` 为 1000–15000，全部 Action 总时长接近 Brief 时长。时长写在这个字段里，不要写进 `description`。
- `state_changes` 记录物品归属、位置、人物状态等要传给后续片段的变化，并用册内 id 点名。

```json
{
  "actions": {
    "actions": [
      {
        "action_id": "action_01",
        "order": 1,
        "scene_id": "scene_attempt",
        "description": "阿舟双手把粽叶卷成筒，拇指把底部按实，随即糯米从缝里漏到桌上，他低头愣住",
        "character_ids": ["character_azhou"],
        "objective": "凭记忆包出四角粽",
        "reaction": "低头看着散开的米，手指停在叶边上",
        "dialogue_lines": ["我明明记得是这样。"],
        "state_changes": ["桌上有散开的粽叶和糯米"],
        "camera_instruction": "中景，机位固定",
        "ambient_audio": "粽叶摩擦、米粒落桌",
        "target_duration_ms": 5000
      }
    ]
  }
}
```
