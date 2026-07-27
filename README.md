# UGC Video Generation Harness

第一阶段 MVP：用户输入一个主题，生成约 1–2 分钟 UGC 视频所需的内容结构与口播剧本。

当前流水线严格按以下顺序执行：

```text
Topic / User Constraints
  → CreativeBrief
  → SectionPlan (Hook / Body / Close)
  → PlannedBeat[]
  → Beat-aware ScriptSegment[]
  → Local Quality Report
```

语音阶段继续执行：

```text
ScriptSegment[]
  → VoicePlan
  → Volcengine TTS WAV segments
  → Native WordAlignment
  → TimedAudio
  → RealizedBeat[]
```

主张与视觉规划阶段使用真实语音形成的 Beat：

```text
RealizedBeat[]
  → Claim Inventory
  → VisualRequirement[]
```

系统不执行自动事实核验。Claim 仅用于理解口播和引导素材方向；网页、文件和
图片保留来源记录，但统一标记为 `verification_status=not_evaluated`。

## 代码结构

```text
src/ugc_harness/
├── shared/                 # 跨阶段基础设施
│   ├── artifacts.py        # 项目产物与 manifest 写入
│   ├── llm.py              # 结构化 LLM 调用
│   ├── llm_prompts.py      # 全局 JSON 与事实规则
│   └── settings.py         # LLM / TTS 配置
├── stage_one/              # 内容结构与剧本
│   ├── models.py
│   ├── prompts.py
│   ├── pipeline.py
│   ├── quality.py
│   └── cli.py
├── stage_two/              # 配音、对齐与 RealizedBeat
│   ├── models.py
│   ├── plan.py
│   ├── tts.py
│   ├── audio.py
│   ├── pipeline.py
│   └── cli.py
├── stage_three/            # 主张与视觉探索方向
│   ├── models.py
│   ├── prompts.py
│   ├── pipeline.py
│   └── cli.py
├── stage_four/             # first-success 素材获取
│   ├── models.py
│   ├── providers.py
│   ├── pipeline.py
│   └── cli.py
├── stage_five/             # 音频驱动的剪辑时间线
│   ├── models.py
│   ├── providers.py
│   ├── pipeline.py
│   └── cli.py
├── stage_six/              # Remotion 最终渲染与媒体质检
│   ├── models.py
│   ├── pipeline.py
│   └── cli.py
└── stage_seven/            # 渲染就绪图片处理
    ├── models.py
    ├── providers.py
    ├── pipeline.py
    └── cli.py
```

这里生成的是声音主导、按信息 Beat 推进的 UGC，而不是电影分镜。第一阶段
不会生成素材、镜头或视频。

时长字段在这一阶段是规划提示，而不是本地硬约束：

- 提示词仍按目标时长生成 Section 和 Beat 的 `target_duration_ms`。
- 本地代码不会强制归一化到目标秒数，也不会因偏差超过某个百分比判失败。
- 只有口播估算明显超出产品的 60–120 秒范围时才给出 warning。
- 最终播放时间由后续 tone、配音语速和词级对齐结果决定。

## 安装

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## API 配置

支持 OpenAI-compatible Chat Completions API。可通过环境变量配置：

```powershell
$env:OPENROUTER_API_KEY = "..."
$env:OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
$env:UGC_LLM_MODEL = "google/gemini-2.5-flash"
```

默认会读取项目根目录的 `.env`。程序只解析 `KEY=value`，不会执行文件：

```text
OPENROUTER_API_KEY="..."
OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
UGC_LLM_MODEL="google/gemini-2.5-flash"
VOLCENGINE_TTS_API_KEY="..."
VOLCENGINE_TTS_ENDPOINT="https://openspeech.bytedance.com/api/v1/tts"
VOLCENGINE_TTS_RESOURCE_ID="volc.service_type.10029"
VOLCENGINE_TTS_VOICE_ID="zh_male_qingshuangnanda_mars_bigtts"
```

`.env` 已加入 `.gitignore`，`.env.example` 可安全提交。

## 运行

```powershell
.\.venv\Scripts\ugc-harness.exe `
  "为什么AI公司开始争夺电力资源" `
  --project-name "AI公司为什么争夺电力" `
  --duration 90 `
  --platform douyin `
  --with-voice `
  --output-root "outputs"
```

常用参数：

- `--duration`：希望模型参考的目标时长。
- `--project-name`：项目文件夹名称；默认使用主题。
- `--audience`：目标观众描述。
- `--goal`：希望观众看完后理解什么。
- `--tone`：可多次传入。
- `--creator-persona`：创作者口吻与身份。
- `--model`：覆盖默认模型。
- `--output-root`：项目输出根目录，默认是 `outputs`。
- `--with-voice`：内容阶段完成后继续生成语音阶段全部产物。
- `--voice-id`：覆盖默认火山 TTS 音色。
- `--fail-on-quality-error`：本地结构质检发现 error 时返回非零退出码。

## 输出

每次生成会写入 `outputs/<项目名>/`。目录结构：

```text
outputs/
└── <项目名>/
    ├── manifest.json
    ├── 01_creative_brief.json
    ├── 02_section_plan.json
    ├── 03_planned_beats.json
    ├── 04_content_plan.json
    ├── 05_script.json
    ├── 06_quality_report.json
    ├── 07_voice_plan.json
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
    ├── video/
    │   ├── final.mp4
    │   └── preview.mp4
    ├── assets/
    └── audio/
        ├── narration.wav
        └── segments/
            ├── vs01.wav
            └── ...
```

其中：

- `brief`：创作约束。
- `planning.sections`：Hook / Body / Close。
- `planning.beats`：认知推进单元、观众状态变化、证据需求、视觉功能提示。
- `script.segments`：与 Planned Beat 显式关联的口播。
- `quality`：宽松时长提示、Beat 覆盖率、证据主张数量和结构问题。
- `stage_one_artifact.json`：以上所有内容的完整聚合版本。
- `manifest.json`：项目产物索引，供后续阶段发现文件。

## 生成配音和 Realized Beats

第一阶段完成后，传入项目目录：

```powershell
.\.venv\Scripts\ugc-voice.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

语音阶段会：

- 根据 CreativeBrief 的 tone 和每段 speech act 生成 `VoicePlan`。
- 每个 ScriptSegment 独立设置语速、能量与前后停顿。
- 调用火山 TTS 输出 24kHz 单声道 WAV。
- 使用 TTS 返回的原生逐字时间戳。
- 将分段 WAV 与停顿拼接成 `audio/narration.wav`。
- 根据真实音频区间将 PlannedBeat 实现为 RealizedBeat。
- 将所有 JSON、完整音频和分段音频登记到 `manifest.json`。

## 生成主张与视觉需求

语音阶段完成后运行：

```powershell
.\.venv\Scripts\ugc-visual-plan.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

这一阶段不执行联网搜索和事实核验。它会：

- 将口播主张分类为事实、观点、推断或修辞。
- 为每个真实音频区间形成一个 `VisualRequirement`。
- 每个视觉需求按顺序提供探索方向，并固定采用 `first_success`：找到第一份
  合格素材后立即停止，不生成或保留 top-k 候选。
- 当前 MVP 不执行联网真实视频检索，避免下载长视频后再进行不可靠的语义裁剪。
  真实视频只在未来作为用户主动提供的预裁剪素材接入。
- 允许 AI 说明性素材作为视觉降级方案，但禁止将它标成证据素材。
- 检查 Beat 视觉需求覆盖率。

## 获取素材

阶段三完成后运行：

```powershell
.\.venv\Scripts\ugc-assets.exe `
  "outputs\AI公司为什么争夺电力"
```

阶段四严格按每个视觉需求的 `directions.order` 执行。某个方向获得第一份
合格素材后立即停止，不继续后续方向，也不保存 top-k。联网页面通过现有
OpenRouter Web Search 查找；来源只做追溯记录，不做事实正确性判断。

当前自动 Provider 支持：

- `source_screenshot`、`document_screenshot`、`chart`、`real_image` 和
  `meme` 才会调用 Web Search；网页截图使用本机 Microsoft Edge。
- 每份 Web 图片落盘后都调用视觉模型做可用性审查。登录、注册、验证码、
  年龄确认、访问认证或订阅登录墙如果遮住主体，文件会立即删除，该方向记录为
  `not_found`，随后继续下一个探索方向。导航栏里的普通登录按钮和不遮挡主体的
  小型 Cookie 提示不会误判为失败。
- `motion_graphic`、`kinetic_typography`、`screen_recording`、
  `talking_head` 和 `ai_image` 直接生成 9:16 AI 视觉帧。
- 只有明确的 `ai_video` 方向才提交异步 AI 视频生成任务。
- 概念性屏幕画面不会伪装成真实产品截图；所有生成素材都写入模型、提示词、
  任务 ID（视频）和生成内容披露标记，且不能作为事实证据。
- 通过审查的 Web 素材会在 `AssetCard.usability_review` 中保存审查模型、
  是否存在认证遮挡、遮挡等级和判定原因。

默认生成模型可以在 `.env` 中替换：

```dotenv
UGC_IMAGE_MODEL="google/gemini-3.1-flash-lite-image"
UGC_VIDEO_MODEL="google/veo-3.1-lite"
UGC_VIDEO_RESOLUTION="720p"
```

## 生成剪辑时间线

阶段四完成后运行：

```powershell
.\.venv\Scripts\ugc-timeline.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

阶段五以 `TimedAudio` 和 `RealizedBeat` 为主时钟，不用预设镜头秒数反向拉伸
旁白。每个 Beat 对应一个主镜头，镜头会覆盖 Beat 后面的自然停顿，字幕继续使用
真实字级时间戳。

- 静态素材只规划轻推、平移、文档聚焦和 9:16 裁切。
- 已有 AI 视频采用 `trim_or_loop_to_audio` 播放策略。
- 如果 Stage 3 选中的方向是 `screen_recording`，会把 Stage 4 的概念 UI
  图片作为首帧，调用 AI 图生视频生成鼠标、点击和滚动交互；不会使用代码伪造
  鼠标动画。
- 来源、AI 生成内容和 interpretation 标签会形成独立 Overlay 时间线。
- 阶段五只生成可执行剪辑计划和派生素材，最终视频编码留给下一渲染阶段。

## 处理渲染图片

Stage 5 完成后运行独立的 Stage 7：

```powershell
.\.venv\Scripts\ugc-images.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

Stage 7 只处理时间线中实际作为静态图片播放的素材，原图不会被覆盖。视觉模型
结合当前 Beat 旁白输出主体框、关键文字、可读性、遮挡状态和推荐策略，本地
Pillow 处理器再生成统一的 1080×1920 JPEG：

- 照片和梗图：围绕人物或核心物体进行主体感知 `cover` 裁切。
- 图表、网页和文档：可靠定位到相关数据或段落时执行 `focus_crop`，抽取重点
  区域作为中央内容卡片，原页面保留清晰版式并降低亮度，不强行把宽内容扩展成包含
  大量无关上下文的 9:16 裁切框。
- AI 生成图片：始终以单层画面铺满输出，可裁剪和推拉，不使用“暗色背景 +
  前景小图”的堆叠布局。
- 必须保留完整页面或定位置信度不足时，使用降低亮度但不模糊的原图背景承托
  完整图片，让观众仍能辨认信息来源和页面结构。
- 已接入图生视频或本身是视频的 Clip 不重复处理。
- 若再次发现登录或认证遮挡，Stage 7 不生成处理图并使质量检查失败。

`25_render_asset_map.json` 保存原始路径到处理后路径的映射，后续渲染器应优先
使用 `render_path`。编号 6 预留给最终渲染阶段，因此本模块保持用户指定的
Stage 7 名称。

## 渲染最终视频

Stage 7 完成后运行：

```powershell
.\.venv\Scripts\ugc-render.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

Stage 6 使用 Remotion 合成 1080×1920、30 FPS 的 H.264/AAC MP4：

- 图片 Clip 优先采用 `25_render_asset_map.json` 中的 Stage 7 处理版本。
- 视频 Clip 使用 Stage 4/5 的 AI 视频并按真实 Beat 区间裁切。
- 图片运动、硬切和 punch cut 来自 Stage 5 时间线。
- 字幕使用真实字级对齐形成的 Caption Cue，固定在底部安全区。
- 来源、AI 生成画面和观点/推断标签按 Overlay 时间线显示。
- `audio/narration.wav` 是唯一声音主时钟，不为了画面修改 TTS 语速。

渲染后使用 Remotion 附带的 FFprobe 检查真实媒体流，并生成 540×960 的
`preview.mp4`。质检要求最终文件为 1080×1920、30 FPS，包含 H.264 视频流和
AAC 音频流，视频流时长与真实音频的误差不超过一帧。AAC 固有的尾部编码填充
会单独记录为 `audio_duration_ms` 和 `container_duration_ms`，不把静音填充误判
为画面时间线超长。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试不调用外部 API；端到端验证需单独运行 CLI。
