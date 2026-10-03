---
name: write-explanations
agent: narrative_agent
description: 教程第三层。前置：已 write_procedure。按需穿插讲解，可空。
---

只在教程首跑、步骤已经落盘后读这篇。

## 前置

已经有 procedure。没有就先写 `write-procedure`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-explanations"}`。
2. `narrative.write_explanations`，带 `explanations`。没有必须说的话就交空 `segments`。
3. 然后激活 `write-shots`。

## 硬性要求

- 只在需要解释原因、判断标准、常见错误或安全约束时写 segment。允许某些步骤没有讲解。
- 这些句子会进对应步骤镜头的 `narration_text`，由独立配音阶段用同一音色 TTS，渲染时叠在操作声上。
- 不要用口播替代画面已经能看懂的操作。
- `step_id` 必须引用已有步骤。
- `placement`：`before_action` / `during_action` / `after_action`，符合讲解真正出现的时机。
- 句子短。
- `explanation_segment_id` 唯一，使用 `te01`、`te02`……。

```json
{
  "explanations": {
    "segments": [
      {
        "explanation_segment_id": "te01",
        "step_id": "step_fold",
        "placement": "during_action",
        "text": "关键不是卷得紧，而是让底部折角完全闭合。"
      }
    ]
  }
}
```
