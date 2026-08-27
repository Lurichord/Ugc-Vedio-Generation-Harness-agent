# UGC Video Generation Harness

内容规划已经由固定 Stage 改造成受 Harness Controller 调度的 Narrative Agent：
用户输入一个主题，Agent 在任务范围、工具白名单、步数/重试预算和输出契约内，
生成约 1–2 分钟 UGC 视频所需的内容结构、口播或剧情表演 Shot。

Narrative Harness 的执行关系为：

```text
Harness Controller
  → TaskEnvelope + RuntimeContext
  → NarrativeAgent
      → spawn stdio Narrative MCP Server
      → tools/list
      → LLM selects narrative.explainer.plan_sections
      → LLM selects narrative.explainer.expand_beats
      → LLM selects narrative.explainer.write_script
      → LLM selects narrative.explainer.compile_shots
  → AgentResult + StatePatch
  → Independent NarrativeCritic
  → Controller Commit + Trajectory
```

Narrative 的默认 CLI 和应用入口使用官方 `mcp` Python SDK v2，通过 stdio 启动
独立 MCP 子进程。Agent 根据 MCP `tools/list` 返回的 Schema 选择工具，Harness 再按
`TaskEnvelope.allowed_tools`、步数和重试预算执行调用并记录 `ActionRecord`。MCP Server
不提交 Project State；状态版本检查、Critic 和 commit 仍由 Harness Controller 负责。
详细接口见 [Narrative stdio MCP 架构](docs/narrative-mcp.md)。

旧的内容规划 Stage 和兼容 Pipeline 已删除。CLI 直接创建
`NarrativeHarnessController` 并调度 Agent，输出 `NarrativeArtifact`；其 Schema 为
`narrative.v3`，包含统一的 Planning、World State 与 ProductionShot 协议。Agent 只提交结构化
Patch；状态版本检查、Critic 验收、下游失效标记和最终提交均由 Controller 负责。

这里的 `VideoWorldState` 是规划阶段生成的视频内容世界，包括实体、主张及其
认知状态、因果关系、待回答问题和叙事边界；它会随 `PlanningArtifact` 一起进入
`ProjectState`，供后续 Agent 读取。当前可用模型、工具和执行约束则单独命名为
`RuntimeContext`，不再占用 World State 的概念。

Voice Agent 执行：

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
├── harness/                # Task/Result/State 合约与 Narrative Controller
├── agents/
│   ├── base.py             # Agent 公共执行约束
│   ├── narrative_agent/    # Narrative 领域的完整实现
│   ├── voice_agent/        # Voice 规划、TTS、对齐与 RealizedBeat
│   ├── editorial_agent/    # Claim 与 A-roll/B-roll 视觉需求
│   ├── asset_agent/        # 素材获取、图片检查与局部修复
│   ├── timeline_agent/     # 音频驱动的剪辑时间线
│   └── render_agent/       # Remotion 最终渲染
├── evaluators/             # 与执行 Agent 分离的 Critic
├── tools/                  # Agent 工具注册表与白名单调用边界
├── shared/                 # 跨阶段基础设施
│   ├── artifacts.py        # 项目产物与 manifest 写入
│   ├── llm.py              # 结构化 LLM 调用
│   ├── llm_prompts.py      # 全局 JSON 与事实规则
│   └── settings.py         # LLM / TTS 配置
```

Explainer 生成声音主导、按信息 Beat 推进的 UGC；Drama 生成角色、场景、动作以及
带原生音轨的 AI 视频 ProductionShot。Narrative Agent 只规划候选产物，不直接生成媒体。

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
$env:VOLCENGINE_ARK_API_KEY = "..."
$env:VOLCENGINE_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
$env:UGC_LLM_MODEL = "doubao-seed-2-0-lite-260215"
$env:UGC_IMAGE_MODEL = "doubao-seedream-5-0-260128"
$env:UGC_VIDEO_MODEL = "doubao-seedance-2-0-260128"
```

默认会读取项目根目录的 `.env`。程序只解析 `KEY=value`，不会执行文件：

```text
VOLCENGINE_ARK_API_KEY="..."
VOLCENGINE_ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
UGC_LLM_MODEL="doubao-seed-2-0-lite-260215"
UGC_IMAGE_MODEL="doubao-seedream-5-0-260128"
UGC_VIDEO_MODEL="doubao-seedance-2-0-260128"
VOLCENGINE_TTS_API_KEY="..."
VOLCENGINE_TTS_ENDPOINT="https://openspeech.bytedance.com/api/v1/tts"
VOLCENGINE_TTS_RESOURCE_ID="seed-tts-2.0"
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
  --production-mode explainer `
  --output-root "outputs"
```

常用参数：

- `--duration`：希望模型参考的目标时长。
- `--project-name`：项目文件夹名称；默认使用主题。
- `--audience`：目标观众描述。
- `--goal`：希望观众看完后理解什么。
- `--tone`：可多次传入。
- `--creator-persona`：创作者口吻与身份。
- `--production-mode`：`auto`、`explainer`、`drama` 或 `tutorial`；三种 Pack 均已安装，
  `auto` 暂时解析为 `explainer`。
- `--video-profile`：`auto`、`a_roll`、`b_roll` 或 `ab_roll`；默认由 AI 判断。
- `--model`：覆盖默认模型。
- `--output-root`：项目输出根目录，默认是 `outputs`。
- `--fail-on-quality-error`：本地结构质检发现 error 时返回非零退出码。

状态推进不再由 CLI flag 控制。Narrative 最终产物通过独立 Critic 后，Controller
会在同一次 commit 中把 Narrative 标记为 `passed`。Explainer 将 `voice_agent` 标记为
`ready`；Drama 使用生成视频原生音轨，Tutorial 使用制作动作原声与按需讲解，二者当前都跳过
Voice/Editorial 并转给 Asset Agent。审核失败则生成回到
`narrative_agent` 的 `revise` transition，并将 `voice_agent` 保持为 `blocked`。

`voice_agent` 被调度后只通过 `voice.create_plan` 和
`audio.synthesize_narration` 白名单工具工作。最终 `VoiceArtifact` 通过独立
Voice Critic 后，Controller 将 `voice_status` 标记为 `passed`、把
`editorial_agent` 标记为 `ready`；失败时生成回到 `voice_agent` 的修订转换。

`editorial_agent` 只通过 `editorial.create_plan` 生成 Claim 与逐 Beat 的
A-roll/B-roll 视觉需求，不负责获取素材。最终 `EditorialArtifact` 通过独立
Editorial Critic 后，Controller 将 `editorial_status` 标记为 `passed`、把
`asset_agent` 标记为 `ready`；失败时回到 `editorial_agent` 修订。当前版本不处理
人物口型与 Voice 音频同步。

`asset_agent` 通过 `asset.acquire_requirement` 按 VisualRequirement 逐项执行
first-success。最终 `AssetArtifact` 通过独立 Asset Critic 后，Controller 将
`asset_status` 标记为 `passed`、把 `timeline_agent` 标记为 `ready`；审核失败时回到
`asset_agent`。Repair Task 只重新获取 `scope.visual_request_ids` 指定的素材，其余已批准
AssetCard 和 VisualResolution 必须保持不变。

Project State 内的 `dependency_graph` 只记录 Agent 之间已经提交的产物边界。Narrative
无论是科普还是剧情，都只提交 `brief / world / profile / artifact:narrative`，不会把
Section、PlannedBeat、ScriptSegment 或剧情 Action 暴露成 Harness 流程节点。Voice 之后
仍可按 AudioSegment、AlignmentSegment、RealizedBeat、Claim 与 VisualRequirement 记录
跨 Agent 依赖。上游内容变化时只把实际后继节点标记为 `stale`。Task 创建时保存
依赖快照，提交前再次核对，避免运行中的旧结果覆盖新状态。图更新采用原子提交，循环依赖、
缺失依赖或 locked 节点覆盖都会整体回滚。

`trajectory.phases` 按 `narrative`、`voice`、`editorial` 等领域阶段保存历史。每个阶段的
`tasks` 不只保留最新结果，而是追加保存生成任务、修订任务、AgentResult、Critic 评价、
Transition 和对应的 GraphUpdate；审核失败也会留下拒绝提交记录，但不会污染 current 图。

`RepairScheduler` 接收到目标节点后会反向裁剪 stale 子图，只选择依赖已经恢复为 current 的
最前沿节点，并按 `produced_by` 合并同一局部分支。生成的 Task 在 `scope.target_refs` 中明确
授权范围，Controller 会拒绝任何 scope 外的语义 hash 变化。修复通过后控制权回到
`repair_scheduler`，由下一轮继续选择 Voice、Editorial 或其他下游修复，而不是跳过 stale
依赖直接消费旧产物。locked 节点会生成 blocker，等待用户处理。

画面模式采用行业语义：`a_roll` 是人物口播主画面，`b_roll` 是画外音配补充
画面，`ab_roll` 是一致人物口播为主并按需插入补充画面。`auto` 会在 Narrative
规划时由 AI 选择。`ab_roll` 下事实证据优先 Web/文档/图表，非证据补充画面可用
梗图或 AI 生成；所有 A-roll 使用同一个 `character_id`，素材获取时会复用已批准
人物素材以保证前后一致。

## 输出

每次生成会写入 `outputs/<项目名>/`。目录结构：

```text
outputs/
└── <项目名>/
    ├── manifest.json
    ├── harness/
    │   ├── narrative_task.json
    │   ├── narrative_agent_result.json
    │   ├── narrative_evaluation.json
    │   ├── narrative_transition.json
    │   ├── voice_task.json
    │   ├── voice_agent_result.json
    │   ├── voice_evaluation.json
    │   ├── voice_transition.json
    │   ├── editorial_task.json
    │   ├── editorial_agent_result.json
    │   ├── editorial_evaluation.json
    │   ├── editorial_transition.json
    │   ├── asset_task.json
    │   ├── asset_agent_result.json
    │   ├── asset_evaluation.json
    │   ├── asset_transition.json
    │   └── project_state.json
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
    ├── asset_inspections.json
    ├── prepared_images.json
    ├── 18_timeline_plan.json
    ├── 19_caption_plan.json
    ├── 20_visual_transform_plan.json
    ├── 21_overlay_plan.json
    ├── 22_timeline_quality_report.json
    ├── 27_render_composition.json
    ├── 28_render_quality_report.json
    ├── narrative_artifact.json
    ├── voice_artifact.json
    ├── editorial_artifact.json
    ├── asset_artifact.json
    ├── timeline_artifact.json
    ├── render_artifact.json
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
- `narrative_artifact.json`：Narrative Agent 内容产物的完整聚合版本。
- `manifest.json`：项目产物索引，供后续阶段发现文件。

## 生成配音和 Realized Beats

Narrative Agent 完成后，传入项目目录：

```powershell
.\.venv\Scripts\ugc-voice.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

Voice Agent 会：

- 根据 CreativeBrief 的 tone 和每段 speech act 生成 `VoicePlan`。
- 每个 ScriptSegment 独立设置语速、能量与前后停顿。
- 调用火山 TTS 输出 24kHz 单声道 WAV。
- 使用 TTS 返回的原生逐字时间戳。
- 将分段 WAV 与停顿拼接成 `audio/narration.wav`。
- 根据真实音频区间将 PlannedBeat 实现为 RealizedBeat。
- 将所有 JSON、完整音频和分段音频登记到 `manifest.json`。

## 生成主张与视觉需求

Voice Agent 审核通过后运行：

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
火山方舟 Web Search 查找；来源只做追溯记录，不做事实正确性判断。

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
UGC_LLM_MODEL="doubao-seed-2-0-lite-260215"
UGC_IMAGE_MODEL="doubao-seedream-5-0-260128"
UGC_VIDEO_MODEL="doubao-seedance-2-0-260128"
VOLCENGINE_ARK_API_KEY="your-ark-api-key"
VOLCENGINE_ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
UGC_VIDEO_RESOLUTION="720p"
```

## 生成剪辑时间线

Asset Agent 审核通过后运行：

```powershell
.\.venv\Scripts\ugc-timeline.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

Timeline Agent 以 `TimedAudio` 和 `RealizedBeat` 为主时钟，不用预设镜头秒数反向拉伸
旁白。每个 Beat 对应一个主镜头，镜头会覆盖 Beat 后面的自然停顿，字幕继续使用
真实字级时间戳。

- 静态素材只规划轻推、平移、文档聚焦和 9:16 裁切。
- 已有 AI 视频采用 `trim_or_loop_to_audio` 播放策略。
- 如果视觉方向是 `screen_recording`，会把 Asset Agent 的概念 UI
  图片作为首帧，调用 AI 图生视频生成鼠标、点击和滚动交互；不会使用代码伪造
  鼠标动画。
- 来源、AI 生成内容和 interpretation 标签会形成独立 Overlay 时间线。
- Agent 只提交可执行剪辑计划和派生素材；Timeline Critic 审核通过后才会进入
  Render Agent。

## Asset Agent 图片审查与修复

图片处理已经并入 Asset Agent。Asset Critic 结合当前 Beat 旁白检查主体框、关键
文字、可读性以及登录或认证遮挡；Critic 本身只给出审核结果，不写文件。对于主体
过小、文本不可读、分辨率不足或尚无竖屏成品等可修问题，Controller 会记录失败
审核并创建 beat/asset 颗粒度的 repair Task，只允许 Agent 调用
`asset.prepare_image`，生成统一的 1080×1920 JPEG 后重新审核：

- 照片和梗图：围绕人物或核心物体进行主体感知 `cover` 裁切。
- 图表、网页和文档：可靠定位到相关数据或段落时执行 `focus_crop`，抽取重点
  区域作为中央内容卡片，原页面保留清晰版式并降低亮度，不强行把宽内容扩展成包含
  大量无关上下文的 9:16 裁切框。
- AI 生成图片：始终以单层画面铺满输出，可裁剪和推拉，不使用“暗色背景 +
  前景小图”的堆叠布局。
- 必须保留完整页面或定位置信度不足时，使用降低亮度但不模糊的原图背景承托
  完整图片，让观众仍能辨认信息来源和页面结构。
- 已接入图生视频或本身是视频的 Clip 不重复处理。
- 若发现登录或认证遮挡，不允许通过裁剪掩盖问题；Critic 要求 Asset Agent 更换
  素材方向。

检查结果和修复成品直接保存在 `asset_artifact.json` 的 `inspections` 与
`prepared_images` 中，同时写入 DependencyGraph 的 `asset_inspection:*` 和
`prepared_image:*` 节点。Timeline Agent 直接使用修复后的 `output_path`。

## 渲染最终视频

Timeline Agent 完成后运行：

```powershell
.\.venv\Scripts\ugc-render.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

Render Agent 通过受限的 `render.execute` 工具调用 Remotion，合成
1080×1920、30 FPS 的 H.264/AAC MP4：

- 图片 Clip 直接采用 Timeline 中已经审核通过的 `playback_path`。
- 视频 Clip 使用 Asset/Timeline Agent 的 AI 视频并按真实 Beat 区间裁切。
- 图片运动、硬切和 punch cut 来自审核通过的 TimelineArtifact。
- 字幕使用真实字级对齐形成的 Caption Cue，固定在底部安全区。
- 来源、AI 生成画面和观点/推断标签按 Overlay 时间线显示。
- `audio/narration.wav` 是唯一声音主时钟，不为了画面修改 TTS 语速。

渲染后，Render Critic 根据 FFprobe 产出的真实媒体信息独立审核，并检查
540×960 的
`preview.mp4`。质检要求最终文件为 1080×1920、30 FPS，包含 H.264 视频流和
AAC 音频流，视频流时长与真实音频的误差不超过一帧。AAC 固有的尾部编码填充
会单独记录为 `audio_duration_ms` 和 `container_duration_ms`，不把静音填充误判
为画面时间线超长。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试不调用外部 API；端到端验证需单独运行 CLI。
