---
name: inspect-graph
description: 用户要改已经做出来的内容。先读精简依赖图，按 dependents_by_agent 判断改画面还是配音，再分次 harness.repair 派给不同 agent。
---

意图层读图、改身份、按层派单。不要自己改 shot / 口播 / 配音正文。一次 `harness.repair` 只派一个制作 agent。改规格用默认 `layer`=`spec`：画面给 narrative，配音给 voice。只改参考图、成片观感或音色时不要走本 skill，下一拍走 `revise-media`。不要把 shot 和 audio 写进同一次 `target_refs`。

还没有 `project_dir` 时不要走这条 skill，先说明还没开工。用户已经点名「第几段 / 那一句」时，下一拍走 `revise-segment`。本 skill 管的是：先拿到图，再决定点哪些节点、分几次派。

图上能被点名的只有这 7 种（一支片子的剧情层只用其中一种名字）：

- `cast` / `prop` / `place`：身份册。节点只存内容（`name` / `description`）；谁用它看边上的 `dependents` 和 `dependents_by_agent`。
- `beat` / `scene` / `step`：同一层，一段剧情
- `utterance`：旁白 / 台词
- `audio`：这句的配音
- `shot`：一镜规格

不要去点 `section` / `action` / `clip`，它们不在图上。

## 步骤

1. `harness.list_graph` 无参：看内容根（`cast` / `prop` / `place` / `beat` / `scene` / `step`）。身份根看 `name`、`dependents`、`dependents_by_agent`（`narrative_agent` 下列 shot，`voice_agent` 下列 audio）。剧情根无参时 dependents 被滤空，不要把这次结果当成全图。
2. 对上用户要动的那一块后，再 `harness.list_graph`，带 `around_ref`（根上的 `beat:…` / `scene:…` / `step:…`，或身份的当前 `name` / `prop:…`）。这次会带上该节点、一跳邻居，以及下游两跳内的 `shot` / `utterance` / `audio`。看每个节点的 `produced_by` 和 `dependents_by_agent`。
3. 换人 / 换物 / 换地：先 `identity.update`。`ref` 填当前 `name`。服务端按名字把同名身份一起改字段，id 不变。不要新建节点，不要改 `binds`。
4. 结合用户这句话和 `dependents_by_agent`，**分次** `harness.repair`（一拍一个工具，默认 `layer`=`spec`）：
   - 只要画面 / 这一镜的说法：`target_refs` 用 `shot:…`（或身份 `changed_refs`，服务端只扩成 shot，交给 narrative）。
   - 只要配音（科普/教程改词后重配）：`target_refs` 用 `audio:…`，交给 voice。
   - 两边都要（换说话人、改口播词后要重配）：先派画面，下一拍再派配音。不要一次塞进两个 agent。
   - 用户其实只嫌图或成片不好看、或只换音色：停，下一拍走 `revise-media`。
5. 加新人 / 新物 / 新地：`identity.create`。要画进现有镜：`shot.bind` 点名那些镜，再用返回的 `shot_refs` 做画面 `harness.repair`。要新开一镜：`shot.create`。
6. 删人 / 删物 / 删镜：`graph.remove`。身份还有 `dependents` 时先删那些镜。

不要把整张图塞进对话：无参一次、around 一次。不要调用 narrative / voice 制作工具。不要为了「多看一点」对同一个坏 ref 重试。

## 怎么选 target_refs

`target_refs` 必须是图上的节点。按用户要改的层来选，优先用 `list_graph` 里实际出现的 `ref` 和 `dependents_by_agent`：

- 改画面、这一镜的说法：`shot:…`（`dependents_by_agent.narrative_agent`）。
- 只重配、不改词：`audio:…`（`dependents_by_agent.voice_agent`）。
- 换人 / 换物 / 换地：先 `identity.update`，再把返回的 `changed_refs` 交给**画面**那一次 `harness.repair`。用户还要换声音时，下一拍再圈对应 `audio:`。
- 改某一段口播：around 该段后圈 `shot:` 改说法；口播变了再圈该段 `utterance` 的 `dependents` 里的 `audio:` 重配。
- 加身份进现有镜：`identity.create` → `shot.bind` → 画面 `harness.repair`。
- 加一镜：`shot.create`。
- 删节点：`graph.remove`。
- 用户没说清改词还是改声音、改镜头：先问一句。
- 节点 `locked=true`：告诉用户这段锁住了，不要 `harness.repair`。
- `status` 不是判断「能不能改」的依据；要改的目标即使是 `current` 也可以派。

一次 `target_refs` 只圈**同一个 agent** 的节点。不要把 `shot` 和 `audio` 混在一次调用里。

## 怎么写 instruction

`instruction` 是给**这一次**制作 agent 的一句话约束，不是给用户看的评论，也不要粘贴整段新正文。

写清楚用户已经说清的：**改哪一层的什么、改成什么样、什么必须保持**。派画面就只写画面/说法；派配音就只写声音，不要在画面那一次里写「同时重配」。

- 差：`改好一点` / `更自然` / `优化画面`
- 好：`把开场口播收成两句，保留原来的钩子问题`
- 好：`只重配第一段，不改词`
- 好：`画面和说法按蜂蜜来，不要改成别的材料`

用户很含糊时，先问一句收成上面这种约束，再 repair。

## 输出格式

本 skill 里每次决策仍是意图层那四个字段：`tool`、`arguments`、`message`、`done`。

### 调工具时的 arguments

每个 JSON 只属于上面写的那个工具，不要串用。

`skill.activate`：

```json
{"name": "inspect-graph"}
```

`harness.list_graph` 无参（空对象）：

```json
{}
```

`harness.list_graph` 带焦点：

```json
{"around_ref": "beat:b3"}
```

也可以 `{"around_ref": "转化糖浆"}` 或 `{"around_ref": "prop:m1"}`。

`identity.update`（换物，id 不变，不换绑）：

```json
{"patches": [{"ref": "转化糖浆", "name": "蜂蜜", "description": "液态蜂蜜"}]}
```

`identity.create`（新身份根）：

```json
{"kind": "prop", "name": "小船", "description": "江上的木船"}
```

`shot.bind`（现有镜只追加身份，接着必须画面 `harness.repair`）：

```json
{"shot_refs": ["shot:shot_a4"], "identity_ref": "prop:prop_1"}
```

`shot.create`（派 generic 写新镜）：

```json
{"anchor_ref": "scene:s2", "binds": ["cast:e1", "prop:prop_1"], "after_ref": "shot:shot_a3", "instruction": "加一镜对月独白"}
```

教程锚点用 `step:…`，讲解用 `beat:…`。`after_ref` 可省略，默认插到最后。

`graph.remove`：

```json
{"ref": "shot:shot_a4"}
```

删身份时 `ref` 可以是 `prop:prop_1` 或当前名字。还有 `dependents` 会失败。

`harness.repair` 画面（身份 ref 或 `shot:`，服务端只扩成 shot，交给 narrative）：

```json
{"target_refs": ["prop:m1"], "instruction": "画面和说法按蜂蜜来", "layer": "spec"}
```

`harness.repair` 配音（只圈 `audio:`，交给 voice）：

```json
{"target_refs": ["audio:ss3"], "instruction": "按男性主持人重配，不改词"}
```

`instruction` 是给制作 agent 的一句话，不是给用户看的。

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
      "ref": "cast:host",
      "kind": "cast",
      "name": "主持人",
      "produced_by": "narrative_agent",
      "status": "current",
      "locked": false,
      "depends_on": [],
      "dependents": ["shot:shot_b3", "audio:ss3"],
      "dependents_by_agent": {
        "narrative_agent": ["shot:shot_b3"],
        "voice_agent": ["audio:ss3"]
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

只认 `ref` / `kind` / `name` / `produced_by` / `status` / `locked` / `depends_on` / `dependents` / `dependents_by_agent` / `has_reference_image` / `has_media`。不要假设还有 hash 或正文。身份根用 `name` 对内容，用 `dependents_by_agent` 把画面和配音拆开派。around 解析失败时改用无参结果里已有的根节点，不要用同一个坏 ref 重试。

### 对用户

`tool` 为 `null`，`done` 为 `false`，`message` 只一句：

- 已派画面：「已经按依赖图把开场那段画面派去改短。」
- 已派配音：「已经把开场那段配音派去重录。」
- 两边都要、刚派完第一层：「画面先改着，配音下一拍再派。」不要谎称配音已经改完。
- 锁住：「开场那段锁住了，改不了。要改别的吗？」
- 分不清改哪一层：「是改台词、只重配，还是改镜头？」
- 图上对不上：「没对上你要改的那一块。是人物、某一段，还是某一镜？」

不要把 `nodes` 念给用户。
