---
name: generate-shot
agent: asset_stage
description: 短剧/教程出镜。全程用 Seedance 参考图模式；由你决定带哪些图，并在 description 里写明这是尾帧还是人物/场景参考。
---

生成一镜前读这篇。激活不等于出片。工具只按你给的 `references` 组包，不会自己改成首尾帧模式。

Seedance 一次请求只能走一种图模式。我们**只用参考图模式**：每张图的 role 都是 `reference_image`。不要要求工具发 `first_frame` / `last_frame`。接镜也是把上一镜尾帧当成一张参考图，用文字说明它是尾帧。

## 步骤

1. 若还没激活过本 skill，先 `skill.activate`，`arguments`：`{"name": "generate-shot"}`。同一任务激活一次即可。
2. `asset.list_shots` 看 `pending` 和每镜的 `materials`。`id`、是否 `ready`、`default_description` 都以这次返回为准。
3. 按 `order` 生成。上一镜该接却还没成片时，先生成上一镜。
4. `asset.generate_shot` 必须自己带 `references`：从该镜 `materials` 里选题，并写 `description`。
5. 范围内 `pending` 为空后再 `asset.submit_candidate`。

## 怎么选 references

`id` 必须是该镜 `materials` 里出现过的。`description` 会写进 Seedance prompt，对应 `@image1`、`@image2`…（按你给的数组顺序）。

- 新开一镜：带上本镜会出镜的人物 / 道具 / 场景身份参考图。
- 接上一镜（`previous_shot_id` 有值）：**第一张**用 `last_frame:{上一镜id}`，写明这是上一镜尾帧、本镜动作从这里连续；后面再跟身份参考图。
- 一张图只说一件事。不要把「尾帧」和「人物设定」写在同一条 description 里。
- 最多 9 张。太多就留本镜真正用到的，不要把无关身份硬塞进去。
- `ready=false` 的不要选。身份图没好就停，不要改成纯文生视频。

## description 怎么写

写成 Seedance 能执行的约束，不要写空话。

- 差：`参考` / `看这张` / `保持一致`
- 好：`上一镜的尾帧。本镜第一个画面要从这里连续，人物姿态、服装、机位和场景不要跳变。`
- 好：`人物阿周的身份参考图。脸、发型、年龄感和这身衣服必须和这张图是同一个人。`
- 好：`厨房场景参考图。空间布局、光线和陈设跟这张图同一处，不要换成别的房间。`

## 调工具时的 arguments

`skill.activate`：

```json
{"name": "generate-shot"}
```

`asset.list_shots`：

```json
{}
```

`asset.generate_shot` 接上一镜：

```json
{
  "shot_id": "shot_a2",
  "references": [
    {
      "id": "last_frame:shot_a1",
      "description": "上一镜的尾帧。本镜动作从这里连续，保持同一人物、服装、机位和场景。"
    },
    {
      "id": "cast:character_azhou",
      "description": "人物阿周的身份参考图。外貌和服装必须与这张图一致。"
    }
  ]
}
```

`asset.generate_shot` 新开一镜：只带身份 `id`，不要编造 `last_frame:`。

## 约束

- 不要改剧本或身份文案。
- 不要自己传本地文件路径；只传 `materials` 里的 `id`。
- 工具报错会原样返回，按错误改 `references` 再试，不要换成文生视频。
