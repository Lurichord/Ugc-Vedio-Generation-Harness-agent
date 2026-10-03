---
name: write-beats
agent: narrative_agent
description: 讲解第二层。前置：已 write_sections。按已批准的三节展开 PlannedBeat。
---

只在讲解首跑、三节已经落盘后读这篇。

## 前置

已经有 section_plan。没有就先写 `write-sections`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-beats"}`。
2. `narrative.write_beats`，带完整 `beats`。
3. 然后激活 `write-script`。

## 硬性要求

- 不得改变 Section 结构、`world_state` 或 `video_profile`。每个 `section_id` 必须已在三节里。
- 拍数按时长：大约 `时长ms/8000` 到 `时长ms/5000`，夹在 6–24。`order` 从 1 连续。
- 全部 `target_duration_ms` 总和接近 Brief 时长，误差不超过 8%。单拍 1500–15000。
- Hook 在前 2 拍建立信息缺口；中段至少一次 attention reset；Close 兑现 Hook，不能只喊口号。
- Close 里至少一拍 `discourse_role` 为 `payoff` 或 `callback`。
- `opening` 只能用于第一拍；其余拍写清与前一拍的语义关系。
- 事实性主张：`evidence_need.required=true`，并写清 `claim_to_verify`。
- `visual_intent_hint` 只写视觉功能方向，不绑定具体素材。科普后面是检索实图/图表 +
  AI 静帧，不要写「对比说明动画」或「一张 Logo」。
- `bind_refs` 用 `cast:` / `prop:` / `place:` 加册内 person/place/product id。没有跨镜主体就 `[]`。概念和机构不要写入。

`discourse_role`：`question` / `expected_answer` / `reveal` / `claim` / `evidence` / `explanation` / `example` / `contrast` / `deepening` / `implication` / `payoff` / `callback`。

`relation_to_previous`：`opening` / `continuation` / `cause` / `contrast` / `escalation` / `evidence_for` / `example_of` / `resolution` / `callback`。

```json
{
  "beats": {
    "beats": [
      {
        "planned_beat_id": "pb01",
        "section_id": "section_hook",
        "order": 1,
        "semantic_goal": "提出观众心里的缺口",
        "discourse_role": "question",
        "relation_to_previous": "opening",
        "target_effect": "好奇",
        "target_duration_ms": 8000,
        "audience_delta": {
          "knowledge_added": [],
          "belief_update": null,
          "question_added": "为什么？",
          "question_resolved": null,
          "emotion_target": "curiosity"
        },
        "evidence_need": {
          "required": false,
          "claim_to_verify": null,
          "acceptable_source_types": []
        },
        "visual_intent_hint": "提出问题的画面",
        "bind_refs": []
      }
    ]
  }
}
```
