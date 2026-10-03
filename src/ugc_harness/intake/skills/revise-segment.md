---
name: revise-segment
description: 用户要改某一段。先对上内容，再读精简依赖图，按 dependents_by_agent 分次派 harness.repair。
---

意图层对上段落、按层派单。换材料/人物先 `identity.update`（`ref` 用当前名字，不要换绑）。加新人/新物用 `identity.create`，进现有镜再 `shot.bind`。不要自己改 shot 正文。一次 `harness.repair` 只派一个制作 agent：改规格时画面圈 `shot:`，配音圈 `audio:`。只改图、成片观感或音色时下一拍走 `revise-media`。

还没有 `project_dir` 时不要走这条 skill，先说明还没开工。用户还没点名哪一段、只说画面/配音/人物不对时，走 `inspect-graph`。

## 步骤

1. `description.list_outline` 对上用户说的「第几段 / 那一段」。看 `order`、`title`、`ref`（大纲 ref，如 `beat:pb01`）。
2. 标题不够确认时，再 `description.get_element` 只取那一个 ref。不要一次取全部 shots。
3. `harness.list_graph` 无参：看内容根（`cast` / `prop` / `place` / `beat` / `scene` / `step`）。身份根看 `name`、`dependents`、`dependents_by_agent`。
4. 对上内容后，再 `harness.list_graph`，带 `around_ref`（可用大纲里的 `beat:…`）。会带上该段的 `shot` / `utterance` / `audio`。
5. 按用户要改的层派 `harness.repair`：画面用该段 `shot:…`；只重配用该段 `audio:…`。两边都要就分两次。换材料/人物先 `identity.update`，再用返回的 `changed_refs` 做画面那一次。加新人/新物：`identity.create` → `shot.bind` → 画面 `harness.repair`。新开一镜：`shot.create`。

不要把整张图塞进对话：无参不要当全图；around 只调一次。不要调用 narrative / voice 制作工具。

## 怎么选 target_refs

`target_refs` 必须是图上的节点。大纲里的 `beat:…` 通常就是图上的 `beat:…`。

按用户要改的层来选，优先用 `list_graph` 里的 `ref` 和 `dependents_by_agent`：

- 改这一段的画面或说法：圈该段挂着的 `shot:…`。
- 只重配、不改词：圈该段 `audio:…`。
- 换材料 / 人物：先 `identity.update`，再把返回的 `changed_refs` 交给画面那一次 `harness.repair`。还要换声音时下一拍再圈 `audio:`。
- 用户没说清改词还是改声音：先问一句，不要两层一起派。
- 节点 `locked=true`：告诉用户这段锁住了，不要 `harness.repair`。
- `status` 不是判断「能不能改」的依据；要改的目标即使是 `current` 也可以派，Harness 会把它弄脏。

一次 `target_refs` 只圈同一个 agent。不要把 `shot` 和 `audio` 混进一次调用。

## 怎么写 instruction

`instruction` 是给**这一次**制作 agent 的一句话约束，不是给用户看的评论，也不要粘贴整段新正文。

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
{"patches": [{"ref": "转化糖浆", "name": "蜂蜜"}]}
```

```json
{"target_refs": ["shot:shot_b3"], "instruction": "把开场口播收成两句，保留钩子问题", "layer": "spec"}
```

```json
{"target_refs": ["audio:ss3"], "instruction": "只重配这一句，不改词"}
```

`identity.update` 的 `ref` 用当前名字。`harness.repair` 的 `target_refs` 必须是图上的 ref。画面用 shot / 身份；配音用 audio。`instruction` 是给制作 agent 的一句话，不要把整段正文写进去。

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
      "dependents": ["shot:shot_a1"],
      "dependents_by_agent": {
        "narrative_agent": ["shot:shot_a1"]
      }
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
      "dependents": ["utterance:ss3", "shot:shot_b3"],
      "dependents_by_agent": {
        "narrative_agent": ["utterance:ss3", "shot:shot_b3"]
      }
    },
    {
      "ref": "utterance:ss3",
      "kind": "utterance",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": ["beat:b3"],
      "dependents": ["audio:ss3"],
      "dependents_by_agent": {
        "voice_agent": ["audio:ss3"]
      }
    }
  ]
}
```

失败：`{"ok": false, "error": "图上没有 beat:missing"}`。

只认 `ref` / `kind` / `name` / `produced_by` / `status` / `locked` / `depends_on` / `dependents` / `dependents_by_agent` / `has_reference_image` / `has_media`。不要假设还有 hash 或正文。around 解析失败时改用无参结果里已有的根节点，不要用同一个坏 ref 重试。

### 对用户

`tool` 为 `null`，`done` 为 `false`，`message` 只一句：

- 已派画面：「已经把第三段画面派去改短。」
- 已派配音：「已经把第三段配音派去重录。」
- 两边都要、刚派完第一层：「画面先改着，配音下一拍再派。」不要谎称配音已经改完。
- 锁住：「第三段锁住了，改不了。要改别的段吗？」
- 对不上：「没对上你说的那一段。是第几段？」
- 分不清改词还是改声音：「是改这段的台词，还是只重配？」

不要把 `nodes` 念给用户。
