---
name: start-project
description: 用户明确要开做。确认 working_brief 够制作使用后派 harness.start_project。
---

看 memory 里的 `working_brief` JSON，不要只看对白。对白里刚说过但 JSON 仍是 null，视为还没写上，禁止开工。

## 什么时候可以调

五项同时非空：

- `topic`
- `communication.goal`
- `audience.description`
- `target.platform`
- `target.duration_target_ms`

已经有 `project_dir` 且 `progress.narrative` 不是 `pending` 时，不要重复开工。

五项有值但按 `clarify-brief` 的标准仍然太薄（goal 只是「讲清楚」、audience 只是「所有人」、topic 只是一个光秃名词）时：不要硬调开工，只针对太薄的那一项问一句，让用户确认更具体的措辞。用户明确说「就按现在的开做」则可以开工，不要再拦。

## 开工之后实际发生什么

`harness.start_project` 只按当前 brief 跑叙事，不会自动做配音和后面的阶段。arguments 必须是 `{}`，不要把 brief 再传一遍。

工具成功：用一句人话告诉用户脚本已经按任务书做好了，并问可不可以继续后面的阶段。不要念剧本、不要报节点。
工具失败：把 `error` 收成一句，例如「还缺目标，先告诉我观众看完该明白什么。」不要把工具 JSON 原文丢给用户，不要用同一参数立刻重试。缺字段时 `done` 仍为 `false`。

## 输出格式

开工条件齐了，先调工具：

```json
{
  "tool": "harness.start_project",
  "arguments": {},
  "message": "",
  "done": false
}
```

工具返回后再对用户说一句：

```json
{
  "tool": null,
  "arguments": {},
  "message": "已经按当前任务书写出脚本。可以继续做配音吗？",
  "done": false
}
```

失败或字段太薄需要再问时，同样 `tool` 为 `null`，只问一件事。
