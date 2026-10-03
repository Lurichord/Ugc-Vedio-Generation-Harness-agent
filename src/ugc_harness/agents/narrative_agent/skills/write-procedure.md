---
name: write-procedure
agent: narrative_agent
description: 教程第二层。前置：已 write_definition。展开可观察的步骤和动作。
---

只在教程首跑、成品定义已经落盘后读这篇。

## 前置

已经有 definition。没有就先写 `write-definition`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-procedure"}`。
2. `narrative.write_procedure`，带完整 `procedure`。
3. 然后激活 `write-explanations`。若几乎不需要口播，讲解可以是空数组，再去 `write-shots`。

## 硬性要求

- `steps` 的 `order` 从 1 连续。每一步写清 `input_state`、`instruction`、`expected_result`。
- 每步输入由上一步输出满足。
- 每一步至少一个 Action。Action 必须是能连续拍摄的手部或工具操作，写到哪只手、怎么动、速度和幅度，不能只是抽象说明。
- `visual_evidence` 写观众应看到什么才能判断该步完成。
- `common_mistakes` 与 `safety_constraints` 只写和该操作直接相关的内容。
- `subject_ref` 必须是身份册里 `kind=product` 的 `entity_id`（与 `materials.material_id` 同一套）。
- `critical_detail` 写手怎么动、以及必须看清的完成条件，不要只写「镜头看清某某」。
- `target_duration_ms` 按真实操作写，全部 Action 总时长接近 Brief 时长。
- 画面以制作动作为主，讲解只能补充原因、判断标准和安全信息。

```json
{
  "procedure": {
    "steps": [
      {
        "step_id": "step_fold",
        "order": 1,
        "instruction": "叠放粽叶并旋转成无孔漏斗",
        "input_state": "两片粽叶平整叠放",
        "expected_result": "漏斗底部完全闭合",
        "visual_evidence": ["底部折角没有透光缝隙"],
        "common_mistakes": ["卷得太松导致底部开口"],
        "safety_constraints": []
      }
    ],
    "actions": [
      {
        "action_id": "action_fold",
        "step_id": "step_fold",
        "description": "双手叠放粽叶，沿三分之一处缓慢旋转成漏斗，拇指把底部折角压实",
        "subject_ref": "mat_leaf",
        "critical_detail": "双手沿三分之一处旋转粽叶，拇指把底部折角压到不透光",
        "target_duration_ms": 8000
      }
    ]
  }
}
```
