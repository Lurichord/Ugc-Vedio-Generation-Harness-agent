---
name: revise-segment
description: 用户要改某一段。先对上内容，再读精简依赖图判断改哪些节点，最后派 harness.repair。
---

意图层只定位和派单。不要自己改正文，不要自己算下游要重跑哪些 stage。

还没有 `project_dir` 时不要走这条 skill，先说明还没开工。

## 步骤

1. `description.list_outline` 对上用户说的「第几段 / 那一段」。看 `order`、`title`、`ref`（大纲 ref，如 `beat:pb01`）。
2. 标题不够确认时，再 `description.get_element` 只取那一个 ref。不要一次取全部 shots。
3. `harness.list_graph` 无参：只看 `artifact:*` 阶段节点的 `status` / `locked` / `produced_by`。
4. 对上内容后，再 `harness.list_graph`，带 `around_ref`（可用大纲里的 `beat:…`，服务端会尝试解析成图上的节点）。只看这一跳邻居。
5. 选定图上真实存在的 `target_refs`，再 `harness.repair`。Harness 自己展开脏子图。

不要把整张图塞进对话：无参不要当全图；around 只调一次。不要调用 narrative 制作工具。

## 怎么选 target_refs

`target_refs` 必须是图上的节点。大纲里的 `beat:…` 只用来对内容；图上常常**没有** `planned_beat:…`，这时不要硬派这个名字。

按用户要改的层来选，优先用 `list_graph` 里实际出现的 `ref`：

- 改某一段台词、结构、长短、加一句钩子：图上有 beat 级节点用它；没有则用 `artifact:narrative`。
- 只重配、不改词：`realized_beat:…` 或 `artifact:voice`。
- 改画面/分镜/素材：`artifact:editorial` / `artifact:assets`，或 around 里对应节点。
- 用户没说清改词还是改声音：先问一句，不要两层一起派。
- 节点 `locked=true`：告诉用户这段锁住了，不要 `harness.repair`。
- `status` 不是判断「能不能改」的依据；要改的目标即使是 `current` 也可以派，Harness 会把它弄脏。

一次 `target_refs` 只圈这一次要动的层，不要把 `dependents` 手工加进去。

## 怎么写 instruction

`instruction` 是给制作 agent 的一句话约束，不是给用户看的评论，也不要粘贴整段新正文。

写清楚三件事里用户已经说清的：**改哪一段的什么、改成什么样、什么必须保持**。

- 差：`改好一点` / `更自然` / `优化第一段`
- 好：`把开场口播收成两句，保留原来的钩子问题，不要加新知识点`
- 好：`把第一段配音拉长，不改词`
- 好：`第三段删掉重复解释，只留一个例子`

用户很含糊时，先对用户问一句收成上面这种约束，再 repair。不要把猜测写成 instruction。

## 输出格式

本 skill 里每次决策仍是意图层那四个字段：`tool`、`arguments`、`message`、`done`。

### 调工具时的 arguments

```json
{"name": "revise-segment"}
```

```json
{}
```

`description.list_outline` 和无参 `harness.list_graph` 都用空对象。

```json
{"ref": "beat:b3"}
```

```json
{"around_ref": "beat:b3"}
```

```json
{"target_refs": ["artifact:narrative"], "instruction": "把开场收成两句，保留钩子问题"}
```

`target_refs` 必须是图上的 ref（或大纲 ref，服务端会解析）。`instruction` 是给制作 agent 的一句话，不要把整段正文写进去。

### 读图（工具返回，按这些字段判断）

无参：

```json
{
  "ok": true,
  "scope": "artifacts",
  "nodes": [
    {
      "ref": "artifact:narrative",
      "kind": "narrative_artifact",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": [],
      "dependents": ["artifact:voice"]
    }
  ]
}
```

`around_ref`：

```json
{
  "ok": true,
  "scope": "neighborhood",
  "around_ref": "beat:b3",
  "resolved_ref": "planned_beat:b3",
  "nodes": [
    {
      "ref": "planned_beat:b3",
      "kind": "planned_beat",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": ["artifact:narrative"],
      "dependents": ["realized_beat:b3"]
    }
  ]
}
```

失败：`{"ok": false, "error": "图上没有 beat:missing"}`。围着 `artifact:*` 看时可能带 `omitted_edges`，表示还有未展开的边，不要据此再扫全图。

只认 `ref` / `kind` / `produced_by` / `status` / `locked` / `depends_on` / `dependents`。不要假设还有 hash 或正文。around 解析失败时改用无参结果里已有的 `artifact:*`，不要用同一个坏 ref 重试。

### 对用户

`tool` 为 `null`，`done` 为 `false`，`message` 只一句：

- 已派修：「已经把第三段派去改短。」
- 锁住：「第三段锁住了，改不了。要改别的段吗？」
- 对不上：「没对上你说的那一段。是第几段？」
- 分不清改词还是改声音：「是改这段的台词，还是只重配？」

不要把 `nodes` 念给用户。
