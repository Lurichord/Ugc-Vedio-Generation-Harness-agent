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

证据与视觉规划阶段使用真实语音形成的 Beat：

```text
RealizedBeat[]
  → Claim Inventory
  → EvidenceRequest[]
  → Research Query[]
  → VisualRequirement[]
```

事实来源和画面素材被分别管理：事实主张必须产生证据检索需求；没有合适的
真实画面时仍可使用 B-roll、动态图形或 AI 说明性素材，但这些素材永远不能
满足证据要求，也不能伪装成新闻现场或真实记录。

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
└── stage_three/            # 主张、证据需求与视觉需求
    ├── models.py
    ├── prompts.py
    ├── pipeline.py
    └── cli.py
```

这里生成的是声音主导、按信息 Beat 推进的 UGC，而不是电影分镜。第一阶段不会生成素材、镜头或视频；事实性主张只会形成待核实的 `evidence_need`，留给下一阶段的 Research / Evidence Agent。

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
    ├── 12_claim_evidence_map.json
    ├── 13_research_queries.json
    ├── 14_visual_requirements.json
    ├── 15_editorial_quality_report.json
    ├── stage_one_artifact.json
    ├── stage_two_artifact.json
    ├── stage_three_artifact.json
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

## 生成主张、证据需求与视觉需求

语音阶段完成后运行：

```powershell
.\.venv\Scripts\ugc-evidence-plan.exe `
  "outputs\AI公司为什么争夺电力" `
  --fail-on-quality-error
```

这一阶段不执行联网搜索，也不会编造已经找到的来源。它会：

- 将口播主张分类为事实、观点、推断或修辞。
- 为所有事实主张生成待执行的 `EvidenceRequest` 和搜索词。
- 为每个真实音频区间形成一个 `VisualRequirement`。
- 允许 AI 说明性素材作为视觉降级方案，但禁止将它作为证据首选素材。
- 分开计算事实证据需求覆盖率和 Beat 视觉需求覆盖率。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试不调用外部 API；端到端验证需单独运行 CLI。
