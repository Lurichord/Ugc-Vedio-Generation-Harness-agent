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
```

`.env` 已加入 `.gitignore`，`.env.example` 可安全提交。

## 运行

```powershell
.\.venv\Scripts\ugc-harness.exe `
  "为什么AI公司开始争夺电力资源" `
  --project-name "AI公司为什么争夺电力" `
  --duration 90 `
  --platform douyin `
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
    └── stage_one_artifact.json
```

其中：

- `brief`：创作约束。
- `planning.sections`：Hook / Body / Close。
- `planning.beats`：认知推进单元、观众状态变化、证据需求、视觉功能提示。
- `script.segments`：与 Planned Beat 显式关联的口播。
- `quality`：宽松时长提示、Beat 覆盖率、证据主张数量和结构问题。
- `stage_one_artifact.json`：以上所有内容的完整聚合版本。
- `manifest.json`：项目产物索引，供后续阶段发现文件。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试不调用外部 API；端到端验证需单独运行 CLI。
