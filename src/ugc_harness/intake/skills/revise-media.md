---
name: revise-media
description: 用户要改参考图、某一镜成片观感或音色，不改剧情和说法。对上地址后 layer=asset 或 layer=voice 派 harness.repair。
---

意图层只分类、对上用户点名的节点，再派一次实现层。不要自己改正文，不要改 spec。不要在 skill 里枚举下游镜或配音；服务端会沿 `identity` / `element` 边扩 scope。

还没有 `project_dir` 时不要走这条 skill。用户要改谁出场、干什么、说什么：走 `inspect-graph` 或 `revise-segment`。改主题/时长/体裁：走 `user-revises`。

## 制作路（用来选 layer，不要念给用户）

- 科普：narrative → voice → editorial → asset → timeline → render。词在 `utterance:`，合成在 `audio:`。
- 演绎：narrative → asset（先身份参考图，再逐镜成片）→ timeline → render。对白在成片里，没有 `audio:`。
- 教程：narrative → asset → voice → timeline → render。讲解词在 `utterance:`，合成在 `audio:`，操作声留在成片里。

## 步骤

1. 用户已经点名「哪张图 / 第几镜 / 换声音」时，用 `description.list_outline` 或一次 `harness.list_graph`（无参或 `around_ref`）对上 `cast:` / `prop:` / `place:` / `shot:` / `audio:`。不必走完整 inspect-graph。
2. 一次 `harness.repair`，带 `layer`：
   - 参考图或某一镜视频：`layer` 为 `asset`。圈身份和或 `shot:`。
   - 科普或教程换音色 / 重配：`layer` 为 `voice`。圈 `audio:`，或圈说话人 `cast:`。
   - 演绎没有 `audio:`。换「声音听着不对」圈那些 `shot:`，`layer` 为 `asset`。
3. `instruction` 写给这一次制作 agent：改成什么样、什么保持。不要贴整段正文。

分不清改说法还是改生成效果时，先问一句。节点 `locked=true` 不要 repair。

## 怎么写 instruction

- 差：`好看一点` / `优化画面`
- 好：`阿周的脸再清楚，衣服不要变`
- 好：`第三镜再亮一点，机位近一点，台词不变`
- 好：`换沉一点的男声，不改词`
- 好：`只重配这一句，不改音色`

## 输出格式

本 skill 里每次决策仍是意图层那四个字段：`tool`、`arguments`、`message`、`done`。

### 调工具时的 arguments

```json
{"name": "revise-media"}
```

```json
{}
```

`description.list_outline` 和无参 `harness.list_graph` 用空对象。

```json
{"around_ref": "shot:shot_a3"}
```

```json
{"target_refs": ["cast:character_azhou"], "instruction": "脸再清楚，衣服不要变", "layer": "asset"}
```

```json
{"target_refs": ["shot:shot_a3"], "instruction": "机位再近一点，保留这句台词", "layer": "asset"}
```

```json
{"target_refs": ["audio:ss3"], "instruction": "换沉一点的男声，不改词", "layer": "voice"}
```

### 对用户

`tool` 为 `null`，`done` 为 `false`，`message` 只一句：

- 已派改图 / 成片：「阿周的图在重画，用到他的镜头会一起重出。」
- 已派改一镜：「第三镜在按你的要求重出。」
- 已派换声：「开场那段在按新音色重配，词不动。」
- 分不清：「是改这一镜的说法，还是只把生成的画面再出一遍？」
