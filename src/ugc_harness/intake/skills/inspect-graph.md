---
name: inspect-graph
description: 用户要改已经做出来的内容。先读精简依赖图；换人/换物/换地先 identity.update，再圈 shot 交给 harness.repair。
---

意图层读图、改身份、圈 shot、派单。不要自己改 shot 正文，不要自己算下游要重跑哪些 stage。

还没有 `project_dir` 时不要走这条 skill，先说明还没开工。用户已经点名「第几段 / 那一句」时，下一拍走 `revise-segment`（先大纲对上段落）。本 skill 管的是：先拿到图，再决定点哪些节点。

图上能被点名的只有这 7 种（一支片子的剧情层只用其中一种名字）：

- `cast` / `prop` / `place`：身份册。节点只存内容（`name` / `description` / `appearance`）；谁用它看边上的 `dependents`。
- `beat` / `scene` / `step`：同一层，一段剧情
- `utterance`：旁白 / 台词
- `audio`：这句的配音
- `shot`：一镜规格

不要去点 `section` / `action` / `clip`，它们不在图上。

## 步骤

1. `harness.list_graph` 无参：看内容根（`cast` / `prop` / `place` / `beat` / `scene` / `step`）。身份根看 `name` 和 `dependents`（含 `shot:`）。剧情根看 `status` / `locked` / `produced_by`。不要把这次结果当成全图。
2. 对上用户要动的那一块后，再 `harness.list_graph`，带 `around_ref`（根上的 `beat:…` / `scene:…` / `step:…`，或身份的当前 `name` / `prop:…`）。只看这一跳邻居。
3. 换人 / 换物 / 换地：先 `identity.update`。`ref` 填当前 `name`（如 `转化糖浆`）。服务端按名字把同名身份一起改字段，id 不变。不要新建节点，不要改 `binds`，不要为同名再写第二条 patch。
4. 再 `harness.repair`。`target_refs` 用上一步返回的 `changed_refs`（服务端会扩成 shot）。制作 agent 只拿到这些 shot 和一句 instruction。

不要把整张图塞进对话：无参一次、around 一次。不要调用 narrative 制作工具。不要为了「多看一点」对同一个坏 ref 重试。

## 怎么选 target_refs

`target_refs` 必须是图上的节点。按用户要改的层来选，优先用 `list_graph` 里实际出现的 `ref`：

- 改画面、这一镜的说法或对白：`shot:…`。
- 换人 / 换物 / 换地：先 `identity.update`，再把返回的 `changed_refs` 交给 `harness.repair`。不要改 `binds`。
- 只重配、不改词：`audio:…`。
- 用户没说清改词还是改声音、改镜头：先问一句。
- 节点 `locked=true`：告诉用户这段锁住了，不要 `harness.repair`。
- `status` 不是判断「能不能改」的依据；要改的目标即使是 `current` 也可以派。

一次 `target_refs` 只圈这一次要动的 shot（或身份 `changed_refs`，或只重配时的 `audio`），不要把 `dependents` 手工再加一遍。

## 怎么写 instruction

`instruction` 是给制作 agent 的一句话约束，不是给用户看的评论，也不要粘贴整段新正文。

写清楚用户已经说清的：**改哪一层的什么、改成什么样、什么必须保持**。

- 差：`改好一点` / `更自然` / `优化画面`
- 好：`把开场口播收成两句，保留原来的钩子问题`
- 好：`只重配第一段，不改词`
- 好：`画面和说法按蜂蜜来，不要改成别的材料`

用户很含糊时，先问一句收成上面这种约束，再 repair。

## 输出格式

本 skill 里每次决策仍是意图层那四个字段：`tool`、`arguments`、`message`、`done`。

### 调工具时的 arguments

```json
{"name": "inspect-graph"}
```

```json
{}
```

无参 `harness.list_graph` 用空对象。

```json
{"around_ref": "beat:b3"}
```

```json
{"patches": [{"ref": "转化糖浆", "name": "蜂蜜", "description": "液态蜂蜜"}]}
```

```json
{"target_refs": ["prop:m1"], "instruction": "画面和说法按蜂蜜来"}
```

`identity.update` 按当前名字改原身份节点的字段，不换绑。`harness.repair` 的 `target_refs` 可以是身份 ref 或 `shot:`（服务端会解析）。`instruction` 是给制作 agent 的一句话。

### 读图（工具返回，按这些字段判断）

无参：

```json
{
  "ok": true,
  "scope": "roots",
  "nodes": [
    {
      "ref": "prop:m1",
      "kind": "prop",
      "name": "转化糖浆",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": [],
      "dependents": ["shot:shot_a1"]
    },
    {
      "ref": "beat:b3",
      "kind": "beat",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": [],
      "dependents": []
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
  "resolved_ref": "beat:b3",
  "nodes": [
    {
      "ref": "beat:b3",
      "kind": "beat",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": [],
      "dependents": ["utterance:ss3", "shot:shot_b3"]
    }
  ]
}
```

失败：`{"ok": false, "error": "图上没有 beat:missing"}`。

只认 `ref` / `kind` / `name` / `produced_by` / `status` / `locked` / `depends_on` / `dependents`。不要假设还有 hash 或正文。身份根用 `name` 对内容，用 `dependents` 找下游 shot。around 解析失败时改用无参结果里已有的根节点，不要用同一个坏 ref 重试。

### 对用户

`tool` 为 `null`，`done` 为 `false`，`message` 只一句：

- 已派修：「已经按依赖图把开场那段派去改短。」
- 锁住：「开场那段锁住了，改不了。要改别的吗？」
- 分不清改哪一层：「是改台词、只重配，还是改镜头？」
- 图上对不上：「没对上你要改的那一块。是人物、某一段，还是某一镜？」

不要把 `nodes` 念给用户。
