# UGC Video Generation Harness：架构与实现说明

## 1. 项目目标

本项目是一个面向 1–2 分钟竖屏 UGC 解说视频的生成 Harness。用户输入一个主题后，系统依次完成：

1. 内容结构规划与口播剧本生成；
2. TTS 配音、字级时间戳和真实音频时间轴；
3. 主张识别与画面需求规划；
4. 联网素材检索或 AI 素材生成；
5. 音频驱动的剪辑、字幕、动画和 Overlay 规划；
6. 静态图片分析与竖屏适配；
7. 视频、配音、字幕和标签的最终合成与质量检查。

这里的目标是声音主导、信息密度较高、有明显创作者口吻的 UGC，而不是强调镜头语言和电影调度的 Film。

## 2. 核心设计原则

### 2.1 音频是唯一主时钟

规划阶段的时长只用于引导模型，不会强迫最终音频严格等于某个预设秒数。真实时长由 TTS 输出决定，后续 Beat、画面、字幕和最终渲染全部服从 `audio/narration.wav`。

这样可以避免口吻要求“急促、有冲击力”，但程序为了满足固定时长又把语音强行拉慢的问题。

### 2.2 Beat 是主要内容和剪辑单位

`PlannedBeat` 表示规划中的语义节点；TTS 完成后，根据真实音频生成 `RealizedBeat`。之后每个 `RealizedBeat` 对应一个主要画面区间。

当前系统不会把每句短口播继续拆成大量 claim 镜头，因此不会因为逐 claim 检索导致几秒内频繁切换画面。

### 2.3 素材采用 first-success

每个画面需求可以包含多个按顺序排列的探索方向。Stage 4 从第一个方向开始执行，找到一份合格素材后立即停止：

```text
direction 1 失败 → direction 2 成功 → 停止
```

系统不保留 top-k 候选，也不会在已有合格素材后继续浪费请求。

### 2.4 事实、观点与素材分开管理

- `ClaimRecord` 用于理解口播属于事实、解释、观点还是修辞。
- `interpretation` 会在画面中显示“观点 / 推断”标签。
- Web 素材保留 URL、标题、发布者和获取时间。
- 当前版本不执行自动事实核验，来源状态固定为 `not_evaluated`。
- AI 图片和视频只能作为说明性画面，不能伪装成事实证据，并显示“AI 生成画面”。
- 找不到真实画面时允许使用 AI 说明性素材，而不是直接删除整段主张。

### 2.5 所有阶段都输出结构化 Artifact

每个阶段通过 Pydantic 严格模型输出 JSON。Artifact 既是阶段间接口，也是可调试、可复现和可审计的中间产物。

## 3. 总体架构

实际执行顺序为：

```text
Stage 1
内容规划与剧本
    ↓
Stage 2
TTS、字级时间戳、RealizedBeat
    ↓
Stage 3
Claim 与 VisualRequirement
    ↓
Stage 4
Web 检索 / AI 素材生成
    ↓
Stage 5
时间线、字幕、动画、Overlay
    ↓
Stage 7
静态图片分析与渲染准备
    ↓
Stage 6
Remotion 最终视频合成
```

Stage 7 的编号是按后续扩展时确定的，但它在 Stage 6 最终渲染之前运行。

### 3.1 主要技术栈

| 模块 | 技术 |
|---|---|
| 结构化生成 | OpenAI 兼容接口、OpenRouter、Pydantic |
| TTS | 火山引擎 TTS |
| HTTP | httpx |
| 图片分析 | 多模态 LLM |
| 图片处理 | Pillow |
| 网页检索 | OpenRouter Web Search |
| 网页截图 | Microsoft Edge Headless |
| AI 图片 | OpenRouter 图片生成接口 |
| AI 视频 | OpenRouter 视频生成接口 |
| 最终合成 | Remotion、React |
| 编码与探测 | H.264、AAC、Remotion 内置 FFmpeg/FFprobe |
| 测试 | pytest |

### 3.2 代码结构

```text
src/ugc_harness/
├── shared/                 # 设置、LLM、ArtifactWriter
├── stage_one/              # 内容规划与剧本
├── stage_two/              # TTS 与真实音频时间轴
├── stage_three/            # Claim 与视觉方向
├── stage_four/             # 素材检索和生成
├── stage_five/             # 剪辑、字幕与动画时间线
├── stage_seven/            # 静态图片处理
└── stage_six/              # 最终渲染

renderer/
├── render.mjs              # Remotion Node 渲染入口
└── src/
    ├── index.jsx
    ├── root.jsx
    └── ugc-video.jsx       # 画面、字幕和 Overlay 组件
```

## 4. 项目输出目录

每个项目拥有独立输出文件夹：

```text
outputs/<项目名>/
├── 01_creative_brief.json
├── 02_content_plan.json
├── 03_planned_beats.json
├── 04_script.json
├── 05_stage_one_quality_report.json
├── 06_voice_plan.json
├── 07_audio_segments.json
├── 08_timed_audio.json
├── 09_word_alignment.json
├── 10_realized_beats.json
├── 11_voice_quality_report.json
├── 12_claim_map.json
├── 13_visual_requirements.json
├── 14_editorial_quality_report.json
├── 15_asset_cards.json
├── 16_visual_resolutions.json
├── 17_asset_quality_report.json
├── 18_timeline_plan.json
├── 19_caption_plan.json
├── 20_visual_transform_plan.json
├── 21_overlay_plan.json
├── 22_timeline_quality_report.json
├── 23_image_analysis.json
├── 24_processed_images.json
├── 25_render_asset_map.json
├── 26_image_quality_report.json
├── 27_render_composition.json
├── 28_render_quality_report.json
├── stage_one_artifact.json
├── stage_two_artifact.json
├── stage_three_artifact.json
├── stage_four_artifact.json
├── stage_five_artifact.json
├── stage_seven_artifact.json
├── stage_six_artifact.json
├── manifest.json
├── audio/
├── assets/
└── video/
    ├── final.mp4
    └── preview.mp4
```

`manifest.json` 登记 JSON、音频、素材和视频的相对路径、文件类型、大小及哈希。

## 5. Stage 1：内容结构规划与剧本生成

### 5.1 输入

用户主题会被封装为 `CreativeBrief`，包含平台、目标时长、受众、表达目标、tone 和创作者人设。

### 5.2 实现方法

`StageOnePipeline` 分两次调用 LLM：

1. 先生成 `PlanningArtifact`；
2. 再根据规划生成 `ScriptArtifact`。

内容规划包括：

- `narrative_pattern`：整体叙事模式；
- `one_sentence_thesis`：一句话核心结论；
- `sections`：固定为 Hook、Body、Close；
- `beats`：具体语义推进节点。

剧本按 Beat 生成 `ScriptSegment`，每段带有：

- `speech_act`；
- 重音词；
- 前后停顿；
- 能量级别。

代码只用 60–120 秒的宽松窗口检查估算时长；提示词仍然保留原来的目标时长表达。若结构或脚本质量不合格，会调用修复提示词再生成一次。

### 5.3 JSON 示例

```json
{
  "brief": {
    "project_id": "ugc_ai_c64c834a",
    "topic": "为什么AI公司开始争夺电力资源",
    "target": {
      "platform": "douyin",
      "duration_target_ms": 90000,
      "aspect_ratio": "9:16"
    },
    "communication": {
      "tone": ["conversational", "information_dense", "slightly_surprising"]
    }
  },
  "planning": {
    "narrative_pattern": "Question→Evidence→Explanation→Implication",
    "one_sentence_thesis": "AI算力需求正在转化为对电力资源的争夺。",
    "beats": [
      {
        "planned_beat_id": "pb01",
        "discourse_role": "question",
        "semantic_goal": "提出反直觉现象",
        "target_duration_ms": 6750
      }
    ]
  },
  "script": {
    "segments": [
      {
        "script_segment_id": "ss01",
        "planned_beat_id": "pb01",
        "text": "你有没有想过，那些搞AI的科技巨头，现在最缺的竟然是电？",
        "delivery_hint": {
          "energy": "high",
          "emphasis_words": ["最缺的", "是电"],
          "pause_after_ms": 180
        }
      }
    ]
  }
}
```

## 6. Stage 2：TTS、字级对齐与 RealizedBeat

### 6.1 输入

- `stage_one_artifact.json`
- TTS API 配置

### 6.2 实现方法

`build_voice_plan()` 根据 tone、speech act、energy 和停顿生成每个语音段的实际朗读参数。

`VolcengineTTS` 按 ScriptSegment 分段请求火山引擎：

- 输出 WAV；
- 采样率默认 24 kHz；
- 单声道；
- 使用 `speed_ratio` 控制语速；
- 请求 `with_timestamp=1` 获取原生字级时间戳。

所有分段 WAV 按 `pause_before_ms` 和 `pause_after_ms` 拼接为：

```text
audio/narration.wav
```

程序以实际 WAV 时长计算全局时间轴。如果 TTS 没有返回字级时间戳，则按照文本 token 和该段真实音频长度生成兜底对齐。

之后，`PlannedBeat` 被实现为 `RealizedBeat`。这里的开始、结束和持续时间来自真实音频，不再使用规划阶段的估算秒数。

### 6.3 JSON 示例

```json
{
  "voice_plan": {
    "segments": [
      {
        "voice_segment_id": "vs01",
        "tone": "好奇、直接，句尾形成明确提问",
        "speed_ratio": 1.12,
        "energy": "high"
      }
    ]
  },
  "timed_audio": {
    "audio_file": "audio/narration.wav",
    "duration_ms": 65755,
    "sample_rate": 24000,
    "channels": 1
  },
  "word_alignment": {
    "words": [
      {
        "word_id": "w0001",
        "word": "你",
        "start_ms": 125,
        "end_ms": 295,
        "confidence": 0.8932
      }
    ]
  },
  "realized_beats": [
    {
      "beat_id": "b01",
      "planned_beat_id": "pb01",
      "start_ms": 0,
      "end_ms": 3955,
      "duration_ms": 3955
    }
  ]
}
```

## 7. Stage 3：Claim 与视觉需求规划

### 7.1 Claim 的作用

Claim 是对口播语义的标注，不等于一个独立镜头。它用于：

- 区分事实、观点、推断和修辞；
- 让视觉规划知道当前 Beat 在表达什么；
- 给 interpretation 自动添加标签；
- 约束 AI 素材不能被标记成事实证据。

一个 Beat 可以包含 Claim，但最终仍然通常只生成一个 `VisualRequirement` 和一个主画面。

### 7.2 VisualRequirement

每个 RealizedBeat 对应一个 `VisualRequirement`，其中包含一个或多个探索方向：

- `description`：想找或生成什么；
- `visual_role`：画面的功能；
- `asset_type`：素材类型；
- `query`：检索词；
- `grounding_requirement`：是否需要精确来源；
- `must_not_imply`：画面不能暗示什么。

选择策略固定为 `first_success`。

### 7.3 JSON 示例

```json
{
  "claims": [
    {
      "claim_id": "c01",
      "beat_id": "b01",
      "statement": "AI科技巨头目前最缺的是电。",
      "claim_type": "factual",
      "importance": 0.9,
      "interpretation_label_required": false
    }
  ],
  "visual_requirements": [
    {
      "visual_request_id": "vr01",
      "beat_id": "b01",
      "purpose": "提出反直觉现象",
      "selection_policy": "first_success",
      "directions": [
        {
          "direction_id": "vr01_d01",
          "order": 1,
          "description": "AI科技公司与电力设施的并置画面",
          "visual_role": "context",
          "asset_type": "motion_graphic",
          "query": "AI tech companies and power grid",
          "grounding_requirement": "contextual"
        }
      ]
    }
  ]
}
```

## 8. Stage 4：素材检索与 AI 素材生成

### 8.1 Provider 路由

`RoutedAssetProvider` 根据 `asset_type` 路由：

| 素材类型 | 获取方法 |
|---|---|
| source_screenshot | Web Search + Edge 网页截图 |
| document_screenshot | Web Search + Edge 网页截图 |
| chart | Web Search + Edge 网页截图 |
| real_image | Web Search + 下载页面主图 |
| meme | Web Search + 下载页面主图 |
| kinetic_typography | AI 图片生成 |
| motion_graphic | AI 图片生成 |
| ai_image | AI 图片生成 |
| screen_recording | 先生成概念 UI 图片 |
| talking_head | AI 图片生成 |
| ai_video | AI 视频生成 |

系统已放弃自动检索真实视频。原因是搜索到的公开视频通常远长于一个 Beat，而当前通用视频模型不适合可靠地完成精确语义裁剪。

### 8.2 Web 素材检索流程

```text
ExplorationDirection
    ↓
OpenRouter Web Search
    ↓
只返回一个公开来源
    ↓
URL 公网安全检查
    ↓
截图网页或下载 og:image
    ↓
视觉模型审查登录/认证遮挡
    ↓
合格：写入 AssetCard
不合格：删除文件并尝试下一个方向
```

Web Search 请求明确要求只返回一个结果，不返回候选列表，也不负责事实核验。

### 8.3 URL 和下载安全

系统只允许 HTTP/HTTPS，拒绝：

- localhost；
- `.local`；
- 私有 IP；
- loopback；
- link-local；
- reserved 和 multicast 地址。

图片最大 20 MB；如果响应不是图片、文件小于 1 KB 或找不到页面 `og:image`，该方向失败。

### 8.4 登录和认证界面审查

网页截图或下载图片完成后，会将图片发送给视觉模型，检查：

- 登录弹窗；
- 注册弹窗；
- 验证码；
- 年龄确认；
- 访问认证；
- 订阅登录墙。

只有遮住主体、导致素材不可使用时才判失败。普通导航栏登录按钮和不遮挡主体的小 Cookie 提示不会误判。

若 `usable=false`：

1. 立即删除本地图片；
2. 当前方向记录为 `not_found`；
3. Pipeline 尝试下一个探索方向。

### 8.5 AI 图片与视频

AI 图片使用 9:16、1K 输出，并保存：

- 生成模型；
- 完整提示词；
- 成本；
- SHA-256；
- AI 披露要求。

AI 视频采用异步任务：

1. 提交生成请求；
2. 按间隔轮询；
3. completed 后下载；
4. failed、cancelled、expired 或超时则失败；
5. 视频不生成音频，最终统一使用 TTS 旁白。

### 8.6 JSON 示例

```json
{
  "assets": [
    {
      "asset_id": "asset_vr01",
      "visual_request_id": "vr01",
      "direction_id": "vr01_d01",
      "beat_id": "b01",
      "modality": "ai_image",
      "origin": "generated",
      "local_path": "assets/generated_image/asset_vr01.jpg",
      "mime_type": "image/jpeg",
      "generated_media_disclosure_required": true,
      "generator_model": "google/gemini-3.1-flash-lite-image",
      "production_ready": true
    }
  ],
  "resolutions": [
    {
      "visual_request_id": "vr01",
      "status": "resolved",
      "selected_direction_id": "vr01_d01",
      "asset_id": "asset_vr01",
      "attempts": [
        {
          "direction_id": "vr01_d01",
          "order": 1,
          "status": "success",
          "reason": "已生成一份 AI 图片素材"
        }
      ]
    }
  ]
}
```

一个 Web 素材的来源和审查字段类似：

```json
{
  "origin": "captured",
  "source": {
    "source_url": "https://example.com/report",
    "publisher": "Example",
    "verification_status": "not_evaluated"
  },
  "usability_review": {
    "reviewer_model": "google/gemini-2.5-flash",
    "usable": true,
    "login_or_auth_overlay": false,
    "obstruction_level": "none",
    "reason": "主体内容未被登录界面遮挡"
  }
}
```

## 9. Stage 5：时间线、字幕和画面动画

### 9.1 音频驱动的 Clip

Stage 5 以 `RealizedBeat` 为基础建立一个 Beat 一个主 Clip 的时间线：

- 第一个 Clip 从 0 开始；
- 当前 Clip 结束于下一个 Beat 的开始；
- 最后一个 Clip 结束于完整音频时长；
- 所有 Clip 必须无缝覆盖完整旁白。

静态图片使用 `hold_to_audio`；视频使用 `trim_or_loop_to_audio`。

### 9.2 概念屏幕动画

如果方向是 `screen_recording`，Stage 4 生成的概念 UI 图片会交给图生视频 Provider，生成滚动、鼠标移动和点击等伪录屏动态。

它是 AI 生成的概念动画，不会伪装成真实产品操作录像。

### 9.3 视觉动画

Stage 5 生成 `VisualTransform`：

- 普通图片：`cover`；
- context/emotion：`slow_pan`；
- 其他图片：`subtle_push`；
- 文档、网页和图表：`contain + document_focus`；
- AI 视频：`native_video`；
- 概念录屏动画：`ai_screen_motion`；
- question、reveal、contrast Beat 可使用 `punch_cut`。

### 9.4 字幕生成

字幕不是 LLM 猜测时间，而是直接使用 Stage 2 的字级时间戳。

`_build_captions()` 按以下条件合并文字：

- 遇到标点且累计文本至少 6 个字符；
- 累计文本达到 14 个字符；
- 当前字幕持续时间达到 2200 ms。

字幕生成 `CaptionCue`，保留 `start_ms`、`end_ms`、文字和关联 `word_ids`。

### 9.5 Overlay

系统生成三类标签：

- `generated_media_disclosure`：AI 生成画面；
- `source_attribution`：来源；
- `interpretation_label`：观点 / 推断。

这些标签拥有独立时间区间，不会写进字幕文本。

### 9.6 JSON 示例

```json
{
  "timeline": {
    "audio_file": "audio/narration.wav",
    "duration_ms": 65755,
    "clips": [
      {
        "clip_id": "clip_01",
        "beat_id": "b01",
        "timeline_start_ms": 0,
        "timeline_end_ms": 4135,
        "playback_path": "assets/generated_image/asset_vr01.jpg",
        "playback_modality": "image",
        "playback_policy": "hold_to_audio",
        "transition_in": "none"
      }
    ]
  },
  "captions": [
    {
      "cue_id": "caption_001",
      "start_ms": 125,
      "end_ms": 875,
      "text": "你有没有想过，"
    }
  ],
  "visual_transforms": [
    {
      "clip_id": "clip_01",
      "fit_mode": "cover",
      "motion_preset": "slow_pan",
      "scale_start": 1.0,
      "scale_end": 1.08
    }
  ],
  "overlays": [
    {
      "overlay_type": "generated_media_disclosure",
      "text": "AI 生成画面",
      "start_ms": 0,
      "end_ms": 2000,
      "position": "top_right"
    }
  ]
}
```

## 10. Stage 7：静态图片分析和竖屏处理

### 10.1 处理范围

Stage 7 只处理 Stage 5 时间线中 `playback_modality=image` 的素材。AI 视频和概念录屏视频不会重复处理。

### 10.2 多模态图片分析

视觉模型结合图片和当前 Beat 旁白输出：

- 内容类型；
- 归一化主体框 `focal_box`；
- 主体定位置信度；
- 是否需要保留完整画面；
- 是否有登录遮挡；
- 文字可读性；
- 与旁白有关的关键文字；
- 推荐处理策略。

### 10.3 图片策略

最终输出统一为 1080×1920 JPEG：

| 策略 | 用途 |
|---|---|
| subject_cover | 围绕主体裁剪并铺满竖屏 |
| portrait_normalize | 普通竖屏标准化 |
| focus_crop | 聚焦图表、网页或文档中的重要区域 |
| contained_background | 完整前景放在降低亮度的背景上 |

当前特别规则：

- AI 生成图片强制使用 `subject_cover`；
- AI 图片可以裁剪并保留推拉动画；
- AI 图片不会使用“暗色大背景 + 前景小图”的双层堆叠布局；
- Web 图表、文档和截图仍可使用 `focus_crop` 或 `contained_background`；
- 背景只降低亮度，不进行模糊，避免观众看不清信息来源；
- 如果再次发现登录或认证遮挡，Stage 7 阻止该图进入渲染。

Stage 7 不覆盖原图，而是生成 `assets/processed_image/`，并通过 `25_render_asset_map.json` 告诉最终渲染器应该使用哪个文件。

### 10.4 JSON 示例

```json
{
  "processed_images": [
    {
      "processed_id": "processed_asset_vr01",
      "asset_id": "asset_vr01",
      "source_path": "assets/generated_image/asset_vr01.jpg",
      "output_path": "assets/processed_image/processed_asset_vr01.jpg",
      "input_width": 768,
      "input_height": 1376,
      "output_width": 1080,
      "output_height": 1920,
      "strategy": "subject_cover",
      "upscaled": true,
      "analysis": {
        "content_type": "illustration",
        "focal_box": [0.0, 0.0, 1.0, 1.0],
        "blocking_overlay": false,
        "text_readability": "good"
      }
    }
  ],
  "render_asset_mappings": [
    {
      "clip_id": "clip_01",
      "original_path": "assets/generated_image/asset_vr01.jpg",
      "render_path": "assets/processed_image/processed_asset_vr01.jpg"
    }
  ]
}
```

## 11. Stage 6：最终视频、配音与字幕合成

Stage 6 是最终渲染阶段，也是整个系统最接近传统视频编辑器的部分。

### 11.1 RenderComposition 构建

Python 端读取：

- Stage 2 的完整旁白；
- Stage 5 的 Clip、字幕、动画和 Overlay；
- Stage 7 的处理后图片映射。

时间从毫秒转换为 30 FPS 帧：

```text
frame = round(milliseconds × 30 / 1000)
```

总帧数使用向上取整，确保画面不会短于音频：

```text
duration_in_frames = ceil(audio_duration_ms × 30 / 1000)
```

图片 Clip 优先使用 Stage 7 的 `render_path`，视频 Clip 使用 Stage 4 或 Stage 5 的视频文件。

### 11.2 临时渲染任务

每次渲染创建唯一 Job 目录：

```text
renderer/public/jobs/<project_id>_<random>/
├── props.json
├── audio/narration.wav
└── media/
```

需要的媒体被复制到 Job 中，Remotion 通过 `staticFile()` 读取。渲染结束后删除临时 Job，项目正式产物保留在 `outputs/<项目名>/video/`。

### 11.3 Remotion 组件结构

`UGCVideo` 的层级为：

```text
AbsoluteFill
├── Audio                        唯一主音轨
├── Sequence[] + ClipMedia      图片或视频
├── Sequence[] + Overlay        来源、AI、观点标签
└── Sequence[] + Caption        烧录字幕
```

#### 图片与视频

- 图片通过 Remotion `Img` 渲染；
- 视频通过 `OffthreadVideo` 渲染；
- 视频静音并按需要循环，因为声音统一来自旁白；
- `objectFit` 使用 Stage 5 的 `cover` 或 `contain`；
- `interpolate()` 根据 Clip 进度实现推近和平移；
- `punch_cut` 在开头 4 帧实现轻微冲击缩放。

#### 配音

```jsx
<Audio src={staticFile(props.audio_path)} />
```

`audio/narration.wav` 是唯一主声音。AI 视频自带声音不会进入最终成片。

#### 字幕

字幕使用 Remotion `Sequence`，按帧控制出现和消失：

```jsx
<Sequence
  from={cue.start_frame}
  durationInFrames={cue.duration_in_frames}
>
  <Caption cue={cue} />
</Sequence>
```

字幕样式：

- 微软雅黑 / 苹方；
- 白色粗体；
- 62 px；
- 黑色半透明圆角底；
- 位于底部安全区；
- 入场时淡入并从 0.97 缩放到 1。

字幕最终被直接烧录进视频画面，不是外挂 SRT，因此任何播放器都能显示，但用户不能手动关闭。

#### Overlay

Overlay 颜色区分：

- 来源：黑底白字；
- AI 生成：黄色底深色字；
- 观点 / 推断：蓝底白字。

Overlay 在首尾执行短淡入淡出。

### 11.4 编码

Node 端调用 Remotion `renderMedia()`：

```text
codec: h264
audioCodec: aac
pixelFormat: yuv420p
crf: 18
x264Preset: medium
concurrency: 2
```

正式输出：

```text
video/final.mp4
```

之后使用 FFmpeg 生成 540×960、CRF 28 的轻量预览：

```text
video/preview.mp4
```

### 11.5 媒体质量检查

Remotion 自带的 FFprobe 检查：

- 最终画面必须为 1080×1920；
- 帧率必须为 30 FPS；
- 必须同时存在视频流和音频流；
- 视频编码为 H.264，音频编码为 AAC；
- Stage 5 时间线必须完整覆盖旁白；
- 所有渲染媒体必须存在；
- 视频流时长与原始旁白时长的误差不能超过一帧，即约 34 ms。

质量检查以视频流时长为准，而不是直接使用 MP4 容器时长。原因是 AAC 编码通常
会在结尾产生少量填充；这部分不是画面真正变长。系统分别记录：

- `video_duration_ms`：视频流时长；
- `audio_duration_ms`：AAC 音频流时长；
- `container_duration_ms`：MP4 容器时长。

例如当前项目的原始 WAV 为 65,755 ms，视频流为 65,767 ms，两者只相差
12 ms；AAC 和容器为 65,813 ms。质量检查通过。

### 11.6 Stage 6 JSON 示例

```json
{
  "schema_version": "render-stage.v1",
  "project_id": "ugc_ai_c64c834a",
  "composition": {
    "renderer": "remotion",
    "renderer_version": "4.0.499",
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "duration_ms": 65755,
    "duration_in_frames": 1973,
    "audio_path": "audio/narration.wav",
    "clips": [
      {
        "clip_id": "clip_01",
        "beat_id": "b01",
        "start_frame": 0,
        "duration_in_frames": 124,
        "media_type": "image",
        "media_path": "assets/processed_image/processed_asset_vr01.jpg",
        "source_path": "assets/generated_image/asset_vr01.jpg",
        "fit_mode": "cover",
        "motion_preset": "slow_pan",
        "scale_start": 1.0,
        "scale_end": 1.08,
        "transition_in": "none"
      }
    ],
    "captions": [
      {
        "cue_id": "caption_001",
        "start_frame": 4,
        "duration_in_frames": 22,
        "text": "你有没有想过，"
      }
    ],
    "overlays": [
      {
        "overlay_id": "overlay_b01_ai",
        "overlay_type": "generated_media_disclosure",
        "text": "AI 生成画面",
        "start_frame": 0,
        "duration_in_frames": 60,
        "position": "top_right"
      }
    ]
  },
  "outputs": [
    {
      "kind": "final",
      "local_path": "video/final.mp4",
      "width": 1080,
      "height": 1920,
      "fps": 30.0,
      "duration_ms": 65767,
      "video_duration_ms": 65767,
      "audio_duration_ms": 65813,
      "container_duration_ms": 65813,
      "video_codec": "h264",
      "audio_codec": "aac",
      "has_video": true,
      "has_audio": true
    }
  ],
  "quality": {
    "passed": true,
    "expected_duration_ms": 65755,
    "actual_duration_ms": 65767,
    "duration_delta_ms": 12,
    "max_allowed_delta_ms": 34,
    "resolution_correct": true,
    "fps_correct": true,
    "audio_present": true,
    "video_present": true,
    "full_timeline_coverage": true,
    "missing_media_count": 0,
    "issues": []
  }
}
```

## 12. Artifact 与 Manifest 设计

每个 Stage 都输出一个完整 Artifact，同时把常用子结构拆成编号 JSON。这样既能
让下一阶段只读取一个 Artifact，也方便人工单独查看某一类数据。

`ArtifactWriter` 负责：

1. 将 Pydantic 对象序列化为 UTF-8 JSON；
2. 写入阶段完整 Artifact；
3. 更新项目根目录的 `manifest.json`；
4. 登记 JSON、音频、图片、视频及其相对路径；
5. 记录阶段状态和质量检查结果。

Manifest 示例：

```json
{
  "schema_version": "artifact-manifest.v1",
  "project_id": "ugc_ai_c64c834a",
  "project_name": "为什么AI公司开始争夺电力资源",
  "stage": "render_complete",
  "render_quality_passed": true,
  "artifacts": [
    {
      "relative_path": "audio/narration.wav",
      "kind": "audio"
    },
    {
      "relative_path": "assets/generated_image/asset_vr01.jpg",
      "kind": "generated_asset"
    },
    {
      "relative_path": "video/final.mp4",
      "kind": "final_video"
    }
  ]
}
```

所有路径都使用项目目录内的相对路径，项目移动或打包后依然可以读取。

## 13. 完整运行方法

### 13.1 安装

```powershell
cd D:\桌面\ugc-vedio-generation-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd renderer
npm install
cd ..
```

Python 负责规划、API 调用、Artifact 和渲染编排；Node.js 只负责 Remotion
合成。

### 13.2 配置

密钥写入项目 `.env`，该文件已通过 `.gitignore` 排除。文档不记录真实密钥。

```dotenv
OPENROUTER_API_KEY="..."
OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
UGC_LLM_MODEL="google/gemini-2.5-flash"

VOLCENGINE_TTS_API_KEY="..."
VOLCENGINE_TTS_VOICE_ID="zh_male_qingshuangnanda_mars_bigtts"

UGC_IMAGE_MODEL="google/gemini-3.1-flash-lite-image"
UGC_VIDEO_MODEL="google/veo-3.1-lite"
UGC_VIDEO_RESOLUTION="720p"
```

也可以通过 `--api-keys-file` 指定另一个配置文件。

### 13.3 逐阶段执行

实际执行顺序是：

```text
Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 5 → Stage 7 → Stage 6
```

Stage 7 编号在 Stage 6 后面，是开发过程中新增的图片准备模块；逻辑上它必须在
最终渲染之前运行。

```powershell
# Stage 1：内容结构和剧本
.\.venv\Scripts\ugc-harness.exe `
  "为什么AI公司开始争夺电力资源" `
  --project-name "为什么AI公司开始争夺电力资源"

# Stage 2：TTS 和真实时间
.\.venv\Scripts\ugc-voice.exe `
  "outputs\为什么AI公司开始争夺电力资源"

# Stage 3：Claim 和视觉方向
.\.venv\Scripts\ugc-visual-plan.exe `
  "outputs\为什么AI公司开始争夺电力资源" `
  --fail-on-quality-error

# Stage 4：素材获取
.\.venv\Scripts\ugc-assets.exe `
  "outputs\为什么AI公司开始争夺电力资源"

# Stage 5：时间线、字幕和动画
.\.venv\Scripts\ugc-timeline.exe `
  "outputs\为什么AI公司开始争夺电力资源" `
  --fail-on-quality-error

# Stage 7：图片处理
.\.venv\Scripts\ugc-images.exe `
  "outputs\为什么AI公司开始争夺电力资源" `
  --fail-on-quality-error

# Stage 6：最终合成
.\.venv\Scripts\ugc-render.exe `
  "outputs\为什么AI公司开始争夺电力资源" `
  --fail-on-quality-error
```

`--fail-on-quality-error` 会在当前阶段质量未通过时返回非零退出码，适合自动化
流水线。

## 14. 阶段之间的数据关系

```text
CreativeBrief
  └─ PlanningArtifact
      ├─ Section[]
      └─ PlannedBeat[]
          └─ ScriptSegment[]
              └─ VoiceSegmentPlan[]
                  ├─ AudioSegment[]
                  ├─ WordTimestamp[]
                  └─ RealizedBeat[]
                      ├─ ClaimRecord[]
                      └─ VisualRequirement[]
                          └─ ExplorationDirection[]
                              ├─ DirectionAttempt[]
                              ├─ VisualResolution
                              └─ AssetCard
                                  ├─ TimelineClip[]
                                  ├─ CaptionCue[]
                                  ├─ VisualTransform[]
                                  └─ OverlayCue[]
                                      └─ ProcessedImage[]
                                          └─ RenderAssetMapping[]
                                              └─ RenderComposition
                                                  └─ final.mp4
```

关键引用关系：

- `planned_beat_id`：Stage 1 的结构 Beat；
- `beat_id`：Stage 2 根据真实音频生成的 Beat；
- `visual_request_id`：每个 Beat 的画面需求；
- `direction_id`：该画面需求的某个探索方向；
- `asset_id`：Stage 4 最终选中的素材；
- `clip_id`：Stage 5 时间线镜头；
- `word_id`：字级时间戳和字幕之间的关联；
- `processed_id`：Stage 7 处理图；
- `RenderAssetMapping.clip_id`：让最终渲染器用处理图替换原图。

## 15. 当前质量控制

### Stage 1

- JSON 必须满足 Pydantic 严格模型；
- Beat 顺序连续；
- Hook、Body、Close 三段齐全；
- Close 至少包含 payoff 或 callback；
- 脚本预估时长使用宽松的 60–120 秒窗口；
- 重音词必须真实出现在文本中。

### Stage 2

- WAV 文件存在且能读取；
- 所有脚本 Segment 均生成音频；
- 字级时间戳覆盖率；
- PlannedBeat 到 RealizedBeat 的覆盖率。

### Stage 3

- 每个 RealizedBeat 恰好有一个 VisualRequirement；
- Claim 和 Beat 引用必须有效；
- interpretation 必须要求显式标签；
- AI 图片和 AI 视频不能标记为证据方向；
- 探索方向顺序必须从 1 连续递增。

### Stage 4

- 每个 VisualRequirement 必须得到一份素材；
- first-success 后不能继续请求；
- 文件必须存在且不为空；
- Web 素材必须通过登录和认证遮挡审查；
- AI 素材必须记录模型和提示词并要求披露；
- 下载 URL 禁止指向本机或内网。

### Stage 5

- Clip 数量必须与 RealizedBeat 数量一致；
- Clip 必须从 0 连续覆盖完整音频；
- 播放素材必须存在；
- 字幕时间必须来自字级对齐；
- 概念屏幕派生视频必须真实落盘。

### Stage 7

- 只处理静态图片 Clip；
- 遮挡图片不能进入渲染；
- 每张合格图片必须生成非空输出；
- AI 图片禁止使用前景小卡片堆叠布局；
- 保留输入尺寸、输出尺寸、策略和哈希。

### Stage 6

- 1080×1920、30 FPS；
- H.264/AAC；
- 音频和视频流都存在；
- 时间线覆盖完整；
- 文件引用全部有效；
- 音画时长误差不超过一帧。

## 16. 当前示例项目结果

项目：

```text
outputs/为什么AI公司开始争夺电力资源/
```

实际结果：

| 指标 | 结果 |
|---|---:|
| 真实旁白时长 | 65,755 ms |
| RealizedBeat | 12 |
| 最终 Clip | 12 |
| 字幕 Cue | 37 |
| 已解决视觉需求 | 12 / 12 |
| Stage 7 静态图片 | 10 |
| 最终分辨率 | 1080×1920 |
| 帧率 | 30 FPS |
| 视频编码 | H.264 |
| 音频编码 | AAC |
| 视频流时长 | 65,767 ms |
| 音画时长差 | 12 ms |
| 最终质量 | 通过 |

最终文件：

```text
outputs/为什么AI公司开始争夺电力资源/video/final.mp4
outputs/为什么AI公司开始争夺电力资源/video/preview.mp4
```

## 17. 当前边界与后续建议

### 17.1 当前明确不做的事情

- 不自动检索长视频再尝试语义裁切；
- 不把 AI 图片或 AI 视频当作事实证据；
- 不执行完整自动事实核验；
- 不把规划阶段的目标秒数强行套到真实口播；
- 不使用 AI 视频自带音频；
- 不输出可关闭的外挂字幕。

### 17.2 素材数据库建议

现阶段不必先建设大型数据库。项目已经通过 `AssetCard` 和 `manifest.json`
记录了素材路径、来源、类型、哈希、生成模型和提示词。下一步可以增加一个跨项目
Asset Library：

- 用 SHA-256 去重；
- 保存主题、实体、视觉角色和 modality 标签；
- 保存来源、授权状态和生成信息；
- 为图片生成视觉 embedding；
- 检索时先查本地库，再执行 Web Search 或 AI 生成；
- 复用前仍执行可用性和画幅检查。

### 17.3 值得继续完善的方向

1. 为用户提供全流程统一 CLI，自动按依赖顺序运行所有 Stage；
2. 增加背景音乐和音效轨，同时确保旁白始终优先；
3. 增加字幕主题、关键词高亮和逐词动画；
4. 增加镜头重复检测，避免连续画面过于相似；
5. 将 Web Search 抽象成可替换 MCP Provider；
6. 增加素材库和跨项目复用；
7. 增加最终视频抽帧视觉审查；
8. 增加失败断点续跑，避免重新调用已经成功的昂贵模型；
9. 为事实主张增加可选的 Research / Citation Stage，而不是强制阻断视觉生成；
10. 将 Stage 7 重编号或在统一编排层隐藏内部编号差异。

## 18. 总结

这个 Harness 的本质不是“输入一句话直接让一个模型生成整条视频”，而是把 UGC
生产拆成一组可以检查、替换和重跑的阶段：

```text
主题
→ 内容结构
→ 口语剧本
→ 真实配音时钟
→ Claim 与画面需求
→ Web 检索或 AI 素材
→ 音频驱动时间线
→ 字幕、标签和图片适配
→ Remotion 最终合成
→ FFprobe 媒体质量检查
```

这种设计保留了 UGC 的口语节奏和信息密度，同时避免电影化分镜系统常见的固定
镜头时长、复杂摄影语言和过度制作。每一步都有 JSON Artifact，因此既适合当前
CLI 运行，也适合以后接入 Agent、MCP、任务队列、素材数据库或可视化编辑器。
