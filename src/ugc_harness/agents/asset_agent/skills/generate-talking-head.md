---
name: generate-talking-head
agent: asset_agent
description: 科普口播镜。自己写发给 Seedance 的导演句，参考图模式出片，TTS 作参考音频。
---

生成一口播镜前读这篇。激活不等于出片。工具只按你给的 `references` 组包，`prompt` 原文发给 Seedance。修片时先读 `list_talking_heads` 里这镜上次的 `generation_prompt`，按本拍 instruction 改那一段。

Seedance 一次请求只能走一种图模式。口播**只用参考图模式**：每张图的 role 都是 `reference_image`。不要要求工具发 `first_frame` / `last_frame`。参考音频由工具自动带上，不要把它放进 `references`。

`list_talking_heads` 每镜有 `continuity.join`：`continue_from_previous`（接）、`fresh`（断）。B-roll 打断之后的下一镜口播是断，不是接。

## 步骤

1. 若还没激活过本 skill，先 `skill.activate`，`arguments`：`{"name": "generate-talking-head"}`。同一任务激活一次即可。
2. `asset.list_talking_heads` 看 `pending`、每镜旁白、`continuity.join` 和 `materials`。人设、动作、旁白都以这次返回为准。
3. 按顺序生成。上一镜该接却还没成片时，先生成上一镜。
4. `asset.generate_talking_head` 必须自己带 `prompt` 和 `references`。
5. 口播镜全部出完后，剩下的 B-roll 用 `asset.acquire_requirement`。范围内 `pending` 为空后再 `asset.submit_candidate`。

## 怎么选 references

`id` 必须是该镜 `materials` 里出现过的。你给出的数组顺序就是「图片1」「图片2」。`description` 只写职务。

- **断** `fresh`：只带 `identity:` 人设参考图。不要编造 `last_frame:`。
- **接** `continue_from_previous`：**第一张**用 `last_frame:{上一镜 visual_request_id}`，职务写「上一镜尾帧」；第二张再跟人设。
- 一张图只说一件事。`ready=false` 的不要选。人设图没有就仍把 `identity:` 带上，工具会先出身份静帧。
- 不要把 `tts:` 放进 `references`。工具会把本 beat 的 TTS 作为 `@音频1`。

## prompt 怎么写

这是发给 Seedance 的整段中文。用本镜旁白和图顺序编译，不要另编口播词，不要写内部 id。

按下面几段写，段与段换行。

1. **限定**：第一句固定 `画面无任何字幕。` 然后写 `9:16 竖屏`、中景或近景、真实室内光线、手机直出。
2. **主体定义**：`将图片N中的[2–3个稳定静态特征]定义为主持人`。特征必须抄 `character_description`，不要另编。后文只用这个名字。人物图是锁脸静帧，写「面部特征参考图片N」。尾帧图不要写成「定义为某人」，必须写成 `图片N是上一镜尾帧`。
3. **任务句**：`参考图片N中的主持人，生成接下来的画面。`
4. **起幅**：
   - **接**：写 `图片1是上一镜尾帧。本镜起幅与图片1同一姿态、同一机位，不要跳变。`
   - **断**：省略这句。
5. **镜头**：机位固定。自然眨眼、轻微点头或手势，一直看着镜头。一种运镜。
6. **声音**：旁白写成 `{list 里的 narration}`，口型与这句话同步。音色、语速和节奏跟 `@音频1` 走，不要另编一种声音。不要配乐压过人声。
7. **锁定**：面部、发型、衣服跟人设图；接镜时房间和光影跟尾帧。结尾写：不要生成任何文字、字幕、Logo、水印。

## 调工具时的 arguments

`skill.activate`：

```json
{"name": "generate-talking-head"}
```

`asset.list_talking_heads`：

```json
{}
```

`asset.generate_talking_head` 新开口播：

```json
{
  "visual_request_id": "vr01",
  "prompt": "画面无任何字幕。\n将图片1中的短发、深色上衣、家庭书房定义为主持人。主持人的面部特征参考图片1。\n参考图片1中的主持人，生成接下来的画面。\n9:16 竖屏中景，真实室内光线，手机直出。\n镜头1：机位固定。主持人看着镜头，自然眨眼，开口说{不同地区的中秋习俗为什么不一样}，口型与这句话同步，音色和节奏跟@音频1走。\n面部、发型、衣服始终与图片1一致。\n不要生成任何文字、字幕、Logo、水印。",
  "references": [
    {"id": "identity:host_main", "description": "口播人设"}
  ]
}
```

`asset.generate_talking_head` 接上一镜：

```json
{
  "visual_request_id": "vr02",
  "prompt": "画面无任何字幕。\n将图片2中的短发、深色上衣、家庭书房定义为主持人。主持人的面部特征参考图片2。\n参考图片2中的主持人，生成接下来的画面。\n9:16 竖屏中景，真实室内光线，手机直出。\n图片1是上一镜尾帧。本镜起幅与图片1同一姿态、同一机位，不要跳变。\n镜头1：机位固定。主持人接着说{因为节俗是地方生活长出来的}，口型与这句话同步，音色和节奏跟@音频1走。\n面部、发型、衣服始终与图片2一致。\n不要生成任何文字、字幕、Logo、水印。",
  "references": [
    {"id": "last_frame:vr01", "description": "上一镜尾帧"},
    {"id": "identity:host_main", "description": "口播人设"}
  ]
}
```

## 约束

- 不要改旁白、人设或编辑部计划。
- 不要自己传本地文件路径；只传 `materials` 里的 `id`。
- `prompt` 不能空。工具报错会原样返回，按错误改 `references` 或 `prompt` 再试，不要改成 `first_frame`，也不要改成纯文生视频。
- B-roll 仍走 `asset.acquire_requirement`，不要把说明静帧做成口播。
