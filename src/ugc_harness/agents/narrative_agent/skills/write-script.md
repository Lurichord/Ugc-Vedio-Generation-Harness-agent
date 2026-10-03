---
name: write-script
agent: narrative_agent
description: 讲解第三层。前置：已 write_beats。为每个 beat 写外部口播。
---

只在讲解首跑、节拍已经落盘后读这篇。

## 前置

已经有 beat_plan。没有就先写 `write-beats`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-script"}`。
2. `narrative.write_script`，带完整 `script`。
3. 然后激活 `write-shots`。

## 硬性要求

- 不得改变 Beat 顺序、ID、论点，不得新增没有规划的核心主张。
- 服从已有 `world_state`。不要把 `to_verify` / `interpretation` / `hypothesis` 改写成无条件成立的事实。
- 每个 Planned Beat 至少 1 个 ScriptSegment；通常一拍一段。
- 总口播正文大约 `时长秒 × 3.2` 到 `时长秒 × 3.8` 个可见字符。
- 前两句短、快、能口播；不要用「大家好，今天我们来聊聊」。
- 句子短，像真人对镜头说话。允许自然转折，不要油腻。
- 每 15–25 秒应有一次新问题、反转、证据或总结性重音。
- 尚未提供来源的事实主张用审慎措辞，不要编造具体数字、采访或亲身经历。
- Close 回扣 Hook，给明确 takeaway；CTA 只有自然适合时才出现。
- `delivery_hint.speech_act` 必须与对应 Beat 的 `discourse_role` 一致。
- `emphasis_words` 必须原样出现在对应 `text` 里。
- `script_segment_id` 用 `ss01`、`ss02`……且唯一。
- `title_options` 至少 3 个。

```json
{
  "script": {
    "title_options": ["标题一", "标题二", "标题三"],
    "segments": [
      {
        "script_segment_id": "ss01",
        "planned_beat_id": "pb01",
        "text": "这一镜要说的口播，把缺口说清楚。",
        "delivery_hint": {
          "speech_act": "question",
          "emphasis_words": ["缺口"],
          "pause_before_ms": 0,
          "pause_after_ms": 180,
          "energy": "high"
        }
      }
    ]
  }
}
```
