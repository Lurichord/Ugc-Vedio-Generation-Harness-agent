---
name: generate-identity
agent: asset_stage
description: 为短剧/教程身份写参考图提示词并出图。修片时先读上次 prompt，再按 instruction 改。
---

画一张身份参考图前读这篇。激活不等于出图。每个 `ref` 只调一次 `asset.generate_identity`，仍是一人/一物/一地一张图，不要拆成大头照和全身照两次出图。

## 步骤

1. 若还没激活过本 skill，先 `skill.activate`，`arguments`：`{"name": "generate-identity"}`。
2. `asset.list_identities` 看范围内谁缺图、谁要重画，以及上次 `generation_prompt`。
3. `asset.generate_identity` 必须自己带 `prompt`。修片时以 memory / 列表里的上次 prompt 为底，按本拍 instruction 改，不要另起一版无关描述。
4. 范围内身份图齐了再出片，或本任务只画图时再 `asset.submit_candidate`。

## prompt 怎么写

竖屏 9:16 静帧。静态特征只用该身份的 `name`、`description`、`appearance_constraints`，不要另编一套，不要写内部 id。不要字幕、不要 logo。不要三视图、不要一张图里多个角度。

按身份种类锁不同的事：

- **人物 `cast:`**：按锁脸来画。无表情，面部清楚，肩部以上占比大，少肩颈、浅底或虚化背景。把 2–3 个静态特征写进句子（发型、衣服领口颜色、体态）。不要全身远景，不要侧面+正面拼贴。
- **场景 `place:`**：锁空间结构和主光。主体占画面主要位置。
- **道具 `prop:`**：锁外形。主体大、居中，形状材料颜色清楚。

## 调工具时的 arguments

```json
{"name": "generate-identity"}
```

人物：

```json
{
  "ref": "cast:character_azhou",
  "prompt": "9:16 竖屏锁脸静帧。中年男人阿舟：短发、深色旧棉衣、中等体态。无表情，面部清楚，肩部以上占比大，浅色虚化背景。不要全身远景，不要三视图，无文字无 logo。"
}
```

场景 / 道具同样每人一次调用，只换锁的内容，例如「旧木桌靠窗、下午侧光、墙边冰箱」或「圆口、白底青花、中等大小」。

## 约束

- 不要改身份文案或镜头规格。
- `prompt` 不能空。
- 不要为同一 `ref` 连出两张图。
