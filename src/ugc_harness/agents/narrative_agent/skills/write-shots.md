---
name: write-shots
agent: narrative_agent
description: 按当前体裁写出或修改 ProductionShot。三种视频公用；字段组合由 shot_kind 锁死。
---

写镜头前读这篇。激活不等于落盘。修片段只改 ProductionShot，不要重写中间层。

先看当前体裁（system / Brief.`production_mode` / 叙事产物里已有哪一层），**只按那一体裁的字段组合交镜**。`shot_kind`、`visual.realization_type`、`audio.audio_mode`、`payload.payload_type` 必须同一套，不要混。

## 前置

- **首跑**：讲解已 `write_script`，短剧已 `write_actions`，教程已 `write_procedure`。身份册必须已在上一层写入。
- **修片段**：叙事产物里已经有全片镜头。只交圈中要改的 `shots`。中间产物只对照，不要重写 section / beat / story / 步骤。
- **只改人设/材料**：已经有全片镜头、用户只要换外观或道具设定时，用 `narrative.write_identities` 交完整身份册，不要在这里写镜头。已绑 id 不要无故更换。

## 步骤

1. 若还没激活过本 skill，先 `skill.activate`，`arguments`：`{"name": "write-shots"}`。
2. `narrative.write_shots`。首跑交全片 `shots`；修片段只交改过的镜，不要带 `identities`。
3. 关系看不清时再 `narrative.list_state`。
4. 首跑镜表齐了再 `narrative.submit_candidate`。

`order` 从 1 连续。`binds` 只能引用身份册里的 `cast:` / `place:` / `prop:`。

## 三种 ProductionShot 怎么组

公共字段：`shot_id`、`order`、`shot_kind`、`purpose`、`source_refs`、`binds`、`visual`、`audio`、`timing`、`payload`。

### 讲解 explainer

从已有 **beats + script** 组装。一拍一镜，至少 6 镜。口播覆盖每一镜。
`visual_source` / `visual_intent` 写检索得来的实景或有出处图表，不要写成对比说明动画或 Logo。

| 槽 | 必须写成 |
|---|---|
| `shot_kind` | `explainer` |
| `source_refs` | `beat:{planned_beat_id}`，口播再加 `utterance:{script_segment_id}` |
| `visual` | `realization_type=explainer`，`visual_source` = 这一拍的画面意图 |
| `audio` | `audio_mode=external_narration`，`script_segment_ids` 覆盖该拍全部口播段。`a_roll`/`ab_roll` 时 `speaker_id` = `video_profile.character_id` |
| `timing` | `duration_driver=narration`，时长跟该拍 `target_duration_ms` |
| `payload` | `payload_type=explainer`：`planned_beat_id`、`script_segment_ids`、`narration_text`、`visual_intent` |

```json
{
  "shot_id": "shot_pb01",
  "order": 1,
  "shot_kind": "explainer",
  "purpose": "提出观众心里的缺口",
  "source_refs": ["beat:pb01", "utterance:ss01"],
  "binds": [],
  "visual": {"realization_type": "explainer", "visual_source": "一个反常识的对比画面"},
  "audio": {"audio_mode": "external_narration", "script_segment_ids": ["ss01"]},
  "timing": {"duration_driver": "narration", "target_duration_ms": 8000},
  "payload": {
    "payload_type": "explainer",
    "planned_beat_id": "pb01",
    "script_segment_ids": ["ss01"],
    "narration_text": "这一镜要说的口播。",
    "visual_intent": "一个反常识的对比画面"
  }
}
```

### 短剧 drama

从已有 **story + actions** 组装。一镜一件事。至少 2 个 `scene_id`、至少 4 镜。对白跟画面一起产生。

| 槽 | 必须写成 |
|---|---|
| `shot_kind` | `drama` |
| `source_refs` | `scene:{scene_id}`、`action:{action_id}` |
| `visual` | `realization_type=generated_scene`：`scene_id`、`character_ids`、`location_id`、`action_description`、`camera_instruction`、`continuity_constraints`。`generation_prompt` 留空 |
| `audio` | `audio_mode=embedded_in_video`：`dialogue_lines`、`ambient_audio` |
| `timing` | `duration_driver=generated_clip`，`target_duration_ms` 1000–15000 |
| `payload` | `payload_type=drama`：`scene_id`、`action_ids`、`dialogue_lines` |

这里写导演卡，不要写发给 Seedance 的全文，也不要写 `图片N`、`{}`、`<>`。

- `visual.action_description`：沿用对应 Action 的可拍句子。主体 + 一个主事件 + 可见反应；写到手、头、肩和幅度。不要从 `purpose` 另编一版。
- `visual.camera_instruction`：**一种景别 + 一种运镜**。不要写死 0–3 秒，不要「近景切到中景」。
- `visual.continuity_constraints`：只写下一镜还得看见的静态物（服装、手里的东西、光），不写剧情解释。
- `visual.generation_prompt`：留空。不要在这里模仿 Seedance 全文；出片由 asset 按图序编译。
- `audio.dialogue_lines`：开口原句，一镜最多 1–2 句；没有对白就空数组。
- `audio.ambient_audio`：绑在动作上的具体声响，不要只写「环境声」。

```json
{
  "shot_id": "shot_a1",
  "order": 1,
  "shot_kind": "drama",
  "purpose": "主角发现门口没人",
  "source_refs": ["scene:scene_door", "action:act_look"],
  "binds": ["cast:character_azhou", "place:location_kitchen"],
  "visual": {
    "realization_type": "generated_scene",
    "scene_id": "scene_door",
    "character_ids": ["character_azhou"],
    "location_id": "location_kitchen",
    "action_description": "阿舟停在厨房门口，右手搭上门把缓慢推门，看见屋里没人，肩膀塌下来，手指停在门把上",
    "camera_instruction": "中景，机位固定",
    "continuity_constraints": ["深色外套一直穿着"],
    "generation_prompt": ""
  },
  "audio": {
    "audio_mode": "embedded_in_video",
    "dialogue_lines": ["怎么又没人？"],
    "ambient_audio": "门轴轻响、冰箱低鸣"
  },
  "timing": {"duration_driver": "generated_clip", "target_duration_ms": 5000},
  "payload": {
    "payload_type": "drama",
    "scene_id": "scene_door",
    "action_ids": ["act_look"],
    "dialogue_lines": ["怎么又没人？"]
  }
}
```

### 教程 tutorial

从已有 **procedure**（以及可选 explanations）组装。每一步至少一镜。讲解可少可无，不要挡住手上动作。有讲解时把原文抄进本镜 `payload.narration_text`，下游配音阶段会按同一音色念，不要在这里写 `图片N` 或配音全文。

| 槽 | 必须写成 |
|---|---|
| `shot_kind` | `tutorial` |
| `source_refs` | `step:{step_id}`、`action:{action_id}`，有讲解再加 `utterance:{explanation_segment_id}` |
| `visual` | `realization_type=procedure_demo`：`step_id`、`action_id`、`subject_ref`、`hands_required`、`camera_angle`（`top_down` / `close_up` / `medium` / `macro`）、`critical_detail` |
| `audio` | `audio_mode=mixed`：`narration_segment_ids`（可空）、`preserve_source_audio=true`、`source_audio_types` |
| `timing` | `duration_driver=demonstration_action` |
| `payload` | `payload_type=tutorial`：`step_id`、`action_ids`、`narration_text`（可空） |

`visual.subject_ref` 与材料册同一套 id。`critical_detail` 写手怎么动、以及镜头必须看清的完成条件，不要只写「看清某某」。一种 `camera_angle`，不要在这一镜里再切景别。

```json
{
  "shot_id": "shot_action_fold",
  "order": 1,
  "shot_kind": "tutorial",
  "purpose": "漏斗底部完全闭合",
  "source_refs": ["step:step_fold", "action:action_fold"],
  "binds": ["prop:mat_leaf"],
  "visual": {
    "realization_type": "procedure_demo",
    "step_id": "step_fold",
    "action_id": "action_fold",
    "subject_ref": "mat_leaf",
    "hands_required": true,
    "camera_angle": "close_up",
    "critical_detail": "双手沿三分之一处旋转粽叶，拇指把底部折角压到不透光"
  },
  "audio": {
    "audio_mode": "mixed",
    "narration_segment_ids": [],
    "preserve_source_audio": true,
    "source_audio_types": ["tool_sound", "material_sound"]
  },
  "timing": {"duration_driver": "demonstration_action", "target_duration_ms": 8000},
  "payload": {
    "payload_type": "tutorial",
    "step_id": "step_fold",
    "action_ids": ["action_fold"],
    "narration_text": null
  }
}
```

## 调工具

`skill.activate`：

```json
{"name": "write-shots"}
```

`narrative.write_shots` 的 `shots` 就是上面这种对象的数组。修片段只交改过的镜，不要带 `identities`。

## 约束

- 不要为了用上 `prior_knowledge` 改剧情。
- 不要生成外部旁白（短剧），不要改配音或渲染。
