---
name: inspect-progress
description: 用户问做到哪了。看 progress 里各阶段状态，用一句话告诉用户。
---

当前 `progress` 往往已经由 Host 从磁盘刷新过。信息够就直接回答，不必调工具。若你认为可能过时，再调 `harness.inspect`。

只讲阶段，不要假装读过剧本，不要念节点或镜头。把枚举收成口语即可：`pending` 还没做，`running` 在做，`ready` / `passed` / `locked` 过了，`failed` / `needs_revision` / `stale` 出了问题。

## 输出格式

需要刷新时：

```json
{
  "tool": "harness.inspect",
  "arguments": {},
  "message": "",
  "done": false
}
```

对用户只回一句阶段，例如：

```json
{
  "tool": null,
  "arguments": {},
  "message": "叙事已经好了，配音还在做。",
  "done": false
}
```

不要念剧本、镜头或节点列表。
