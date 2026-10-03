---
name: generate-shot
agent: asset_stage
description: 短剧/教程出镜。读镜头和依赖图，自己写发给 Seedance 的导演句，再按参考图顺序出片。
---

生成一镜前读这篇。激活不等于出片。工具只按你给的 `references` 组包，`prompt` 原文发给 Seedance。修片时先读 memory / `list_shots` 里这镜上次的 `generation_prompt` 和 `generation_references`，按本拍 instruction 改那一段，不要从规格重新发明一版导演句。

Seedance 一次请求只能走一种图模式。我们**只用参考图模式**：每张图的 role 都是 `reference_image`。不要要求工具发 `first_frame` / `last_frame`。人物身份仍是每人一张参考图，不要拆成大头照和全身照。

`list_shots` 每镜有 `continuity.join`：`continue_from_previous`（接）、`bridge_from_previous`（承）、`fresh`（断）。不要只看 `previous_shot_id` 有没有值——接和承都有上一镜 id，选图和起幅写法不一样。

## 步骤

1. 若还没激活过本 skill，先 `skill.activate`，`arguments`：`{"name": "generate-shot"}`。同一任务激活一次即可。
2. `asset.list_shots` 看 `pending`、每镜正文、`continuity.join` 和 `materials`。动作、镜头、台词、身份名都以这次返回为准，不要去改剧本。
3. 关系看不清时，再 `asset.list_graph`：无参看内容根；`around_ref` 用 `shot:镜头id`、`cast:` / `prop:` / `place:`，或身份当前名字。图上只有谁依赖谁、叫什么，没有「谁拿着谁」这种谓词。
4. 按 `order` 生成。上一镜该接或该承却还没成片时，先生成上一镜。
5. `asset.generate_shot` 必须自己带 `prompt` 和 `references`。
6. 范围内 `pending` 为空后再 `asset.submit_candidate`。

## 怎么选 references

`id` 必须是该镜 `materials` 里出现过的。你给出的数组顺序就是「图片1」「图片2」。`description` 只写职务。

按 `continuity.join` 选图：

- **断** `fresh`：只带本镜会出镜的人物 / 道具 / 场景身份参考图。不要编造 `last_frame:`。
- **接** `continue_from_previous`：**第一张**用 `last_frame:{上一镜id}`，职务写「上一镜尾帧」；后面再跟身份参考图。
- **承** `bridge_from_previous`：尾帧要带，但**不要放第一张**。先放人物 / 场景 / 道具身份图，尾帧放后面。职务仍写「上一镜尾帧」。
- 优先级（接：尾帧最先；承：身份图先于尾帧）：人物 → 场景 → 本镜真正入画的道具。没入画的不要带。目标 4–5 张，不要为了材料里有就堆到 9 张。
- 一张图只说一件事。最多 9 张。`ready=false` 的不要选。身份图没好就停，不要改成纯文生视频。

## prompt 怎么写

这是发给 Seedance 的整段中文。用本镜正文和图顺序编译，不要另编剧情，不要写内部 id。

材料从哪来：

- 短剧动作用 `visual.action_description`，教程动作用 `visual.critical_detail`，不要用 `purpose` 当动作。
- 短剧台词用 `audio.dialogue_lines`。教程讲解不写进 Seedance：`audio.narration_text` 只是对照，配音阶段会用同一音色 TTS。本镜第 6 段只留现场操作声。
- 景别 / 运镜用 `visual.camera_instruction` 或 `visual.camera_angle`。只有「中景」就写机位固定，不要发明推近。
- 人名、物名、地名用 `identities` / `materials` 里的 `name`。
- 风格和光影跟场景身份走，不要每镜换一套「电影感」。
- 总时长是生成参数，不要在正文里写 `0–3秒` 排演。

按下面七段写，段与段换行。不必套死用词，但顺序不要打乱。

1. **限定**：第一句固定 `画面无任何字幕。` 然后写 `9:16 竖屏`、景别、真实室内光线、手机直出。
2. **主体定义**：`将图片N中的[2–3个稳定静态特征]定义为阿舟`。特征必须抄身份册 `description` / `appearance_constraints`，不要另编。场景、道具同样定义一次。后文只用这个名字，不要每个动词都重复「图片N中的阿舟」。人物图是锁脸静帧，写「面部特征参考图片N」。尾帧图不要写成「定义为某人」，必须写成 `图片N是上一镜尾帧`。
3. **任务句**：`参考图片N中的阿舟，生成接下来的画面。` 不要写「向后延长视频」或「参考图片1的运镜」——尾帧图不是视频延长。
4. **起幅**：接和承都要点名尾帧，不要只写「延续光影」。
   - **接**：写 `图片1是上一镜尾帧。本镜起幅与图片1同一姿态、同一机位，不要跳变。`（尾帧是图片1时）。
   - **承**：写 `图片N是上一镜尾帧，只用来延续妆造、服装、材料外观、场景结构和光影，不要沿用上一镜的站位和机位。` N 是尾帧的序号。
   - **断**：省略这句。
5. **镜头节拍**：用 `镜头1` / `镜头2` 写先后。每条：运镜 → 主体动作 → 空间变化。一种运镜。先后用「先 / 随即 / 片刻后」，不写秒数。
6. **声音符号**：短剧台词写成 `{怎么又没人？}`，口型与这句话同步。教程不要写画外旁白、不要对口型，只写 `<现场操作声>`。音效写成 `<门轴轻响>`。不要写配乐、BGM、氛围音乐，也不要用全角括号 `（轻缓室内氛围）` 这类配乐符号。短剧音色跟身份册走。
7. **锁定与约束**：面部、发型、衣服跟人物图；房间布局跟场景图；道具只锁外形。结尾写：不要生成任何文字、字幕、Logo、水印。不要生成背景音乐或BGM。双人镜点名对应图，并写「全程不要出现第二个外形相同的人」。

句里已经出现的道具名，才写成「图片N中的碗」。人和物同时出场，不要自动写成「他拿着碗」。

## 调工具时的 arguments

`skill.activate`：

```json
{"name": "generate-shot"}
```

`asset.list_shots`：

```json
{}
```

`asset.list_graph` 看一镜周围：

```json
{"around_ref": "shot:shot_a2"}
```

`asset.generate_shot` 接上一镜：

```json
{
  "shot_id": "shot_a2",
  "prompt": "画面无任何字幕。\n将图片2中的短发、深色旧棉衣、中等体态定义为阿舟。阿舟的面部特征参考图片2。\n将图片3中的旧木桌靠窗、下午侧光、墙边冰箱定义为老厨房。\n参考图片2中的阿舟，生成接下来的画面。\n9:16 竖屏中景，真实室内侧光，手机直出。\n图片1是上一镜尾帧。本镜起幅与图片1同一姿态、同一机位，不要跳变。\n镜头1：机位固定。阿舟看向门口，肩膀微沉。\n镜头2：他开口说{怎么又散了？}，口型与这句话同步，中年男声、语气发闷。\n<冰箱低鸣>\n面部、发型、衣服始终与图片2一致；房间布局与图片3一致。\n不要生成任何文字、字幕、Logo、水印。不要生成背景音乐或BGM。",
  "references": [
    {"id": "last_frame:shot_a1", "description": "上一镜尾帧"},
    {"id": "cast:character_azhou", "description": "人物阿舟"},
    {"id": "place:location_kitchen", "description": "场景老厨房"}
  ]
}
```

`asset.generate_shot` 承上一镜：尾帧不要放第一张；起幅不要锁站位机位。

```json
{
  "shot_id": "shot_a5",
  "prompt": "画面无任何字幕。\n将图片1中的短发、深色旧棉衣、中等体态定义为后羿。后羿的面部特征参考图片1。\n将图片2中的庭院石台、白日侧光定义为后羿家院。\n参考图片1中的后羿，生成接下来的画面。\n9:16 竖屏中景，真实室外侧光，手机直出。\n图片3是上一镜尾帧，只用来延续妆造、服装、材料外观、场景结构和光影，不要沿用上一镜的站位和机位。\n镜头1：机位固定。后羿停在空荡院中，弓垂在身侧。\n镜头2：他开口说{晚了。}，口型与这句话同步。\n面部、发型、衣服始终与图片1一致。\n不要生成任何文字、字幕、Logo、水印。不要生成背景音乐或BGM。",
  "references": [
    {"id": "cast:character_houyi", "description": "人物后羿"},
    {"id": "place:location_courtyard", "description": "场景后羿家院"},
    {"id": "last_frame:shot_a4", "description": "上一镜尾帧"}
  ]
}
```

`asset.generate_shot` 新开一镜：只带身份 `id`，不要编造 `last_frame:`。教程同七段；第 6 段只写操作声，不要把讲解写进 prompt。

```json
{
  "shot_id": "shot_action_fold",
  "prompt": "画面无任何字幕。\n将图片1中的翠绿粽叶、三分之一处折痕定义为粽叶。\n参考图片1中的粽叶，生成接下来的画面。\n9:16 竖屏特写，真实室内光线，手机直出。\n镜头1：机位固定。双手沿三分之一处旋转粽叶，拇指把底部折角压到不透光，双手清楚入镜。\n<现场操作声>\n粽叶的形状与图片1一致。\n不要生成任何文字、字幕、Logo、水印。不要生成背景音乐或BGM。",
  "references": [
    {"id": "prop:mat_leaf", "description": "道具粽叶"}
  ]
}
```

## 约束

- 不要改剧本或身份文案。
- 不要自己传本地文件路径；只传 `materials` 里的 `id`。
- 不要生成背景音乐或 BGM；成片只保留对白和现场音效。
- `prompt` 不能空。工具报错会原样返回，按错误改 `references` 或 `prompt` 再试，不要换成文生视频。
