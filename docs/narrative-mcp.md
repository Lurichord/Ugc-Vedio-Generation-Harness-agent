# Narrative stdio MCP 架构

Narrative Agent 使用官方 MCP Python SDK v2，并通过 stdio 与独立的 Narrative MCP
Server 通信。CLI 和应用后端不直接调用 Narrative 生成函数。

## 执行边界

```text
NarrativeHarnessController
  → Format Pack 选择格式并创建完整 TaskEnvelope
  → 启动 python -m ugc_harness.mcp_servers.narrative
  → MCP initialize + tools/list
  → narrative.configure_task（初始化 MCP Server task session，不计入 Agent 步数）
  → 模型在每一步看到 allowed_tools ∩ 已发现工具 的完整集合，
    自主决定调用哪个工具、传什么参数
  → MCP tools/call（使用模型给出的 arguments）
  → 工具结果或错误作为消息反馈给模型，由模型决定继续、重试或修改哪里
  → 模型主动调用 narrative.submit_candidate 结束内部工作
  → AgentResult
  → NarrativeCritic
  → Controller commit
```

Harness 保留的守护职责（不属于模型决策）：

- 只暴露 TaskEnvelope `allowed_tools` 允许且服务端确实存在的工具；
  模型选择集合之外的工具会被直接拒绝。
- 步数预算 `max_steps` 与单工具重试上限 `max_retries` / `fallback_policy`。
- 预检失败时若模型漏传 `problems` 参数，Harness 会强制补上，保证修复调用
  始终携带预检结论。
- 最终验收（Pydantic Schema、预检、NarrativeCritic）与 State 提交。

stdio 子进程以一个 Task 为生命周期。MCP Server 只保存工具产生的草稿；Agent 根据自己的
消息历史和 `ActionRecord` 自主规划，不存在 Snapshot、阶段状态、revision 或级联失效表。
API Key 和 Base URL 通过子进程环境传递，不放进模型可见的 Tool arguments。

## 当前工具

科普（explainer）Workflow 的细粒度工具集，对应
`docs/多类型视频Agent架构方案.md` §7.2：

- `narrative.configure_task`：设置 Brief、模型和局部修复上下文；由 Agent 调用，
  不暴露给模型选择。
- `narrative.submit_candidate`：由模型在认为工作完成时主动调用；提交不完整或结构不一致时
  直接返回具体错误，Agent 自己决定调用哪个工具修复。
- `narrative.explainer.plan_sections`：生成或修复 VideoWorldState、video_profile
  与三段式 Section 骨架（SectionPlanArtifact）。
- `narrative.explainer.expand_beats`：基于已批准的 Section 骨架展开 Planned Beats
  （BeatPlanArtifact）；服务端会组装并校验完整 PlanningArtifact。
- `narrative.explainer.write_script`：基于组装后的 Plan 生成或修复口播 Script。
- `narrative.explainer.compile_shots`：把已批准的 Plan 与 Script 确定性编译为
  ProductionShot 列表（ShotPlanArtifact）；不发起模型调用。

剧情（drama）Workflow：

- `narrative.drama.design_world`：生成角色、地点、关键物品、人物目标和连续性约束；
- `narrative.drama.plan_story`：把 World State 展开为具有因果与情绪转折的 Scene；
- `narrative.drama.expand_scenes`：把 Scene 展开为可连续生成的表演 Action、对白和状态变化；
- `narrative.drama.compile_shots`：确定性编译为 `generated_scene + embedded_in_video +
  generated_clip` ProductionShot，不发起额外模型调用。

工具可以重复调用，MCP Server 不维护固定 Stage，也不因上游修改而自动清空下游草稿。
某个工具确实缺少自身必需输入时只返回领域错误；调用顺序和修复范围始终由 Agent 决定。

## Format Pack 与统一 Shot 协议

`CreativeBrief.production_mode` 支持 `auto`、`explainer`、`drama` 和 `tutorial`。
只有 Harness 通过 `NarrativeFormatRegistry` 解析模式，并由所选 Pack 决定：

- TaskEnvelope 的工具白名单；
- Agent system instructions；
- 必须产出的 artifact 集合；
- 步数和重试预算。

Pack 不参与 Agent 的执行循环，也不保存当前运行状态。它只是 Harness 内部的格式策略，
负责把上述决策固化进 `TaskEnvelope`。Agent 收到任务后只读取 `format_id`、
`agent_instructions`、`allowed_tools`、`required_outputs` 和 `budget`，因此 Pack 与
TaskEnvelope 不形成两份并行的运行合同。

当前默认 Registry 已安装 `ExplainerFormatPack`、`DramaFormatPack` 和
`TutorialFormatPack`；`auto` 暂时解析为 `explainer`。

`NarrativeArtifact` 已升级为 `narrative.v3`。Planning 使用 `planning_type` 判别联合；
ProductionShot 的公共外层包含：

- `visual`：按 `realization_type` 区分 explainer、generated_scene、procedure_demo；
- `audio`：按 `audio_mode` 区分 external_narration、embedded_in_video、mixed；
- `timing`：由 narration、generated_clip、demonstration_action 或 fixed 驱动；
- `payload`：保留各 Format Pack 自己的细节字段。

Explainer 编译器输出 `external_narration + narration-driven` Shot。Drama 已实现完整
MCP 工具链、确定性 Shot 编译和 `DramaCritic`，输出视频模型原生音轨，不进入 Voice Agent；
Script、Voice、Editorial 状态记为 `not_required`，通过验收后控制权转给 Asset Agent。
Tutorial 已接入结果定义、步骤/动作规划、按需讲解、确定性 Shot 编译和
`TutorialCritic`。其 Shot 使用 `procedure_demo + mixed + demonstration_action`，
以制作动作和现场声音为主，讲解只在必要处穿插；当前通过验收后直接转给 Asset Agent。

MCP Server 的 `NarrativeTaskSession` 只是一个轻量草稿容器：

- `ExplainerFormatState`：SectionPlan、BeatPlan；
- `DramaFormatState`：Character、Scene、Action 草稿；
- `TutorialFormatState`：结果定义与步骤/动作草稿。

当前 stdio Server 会按 Task 实例化对应格式的草稿容器，但不会向 Agent生成状态快照，
也不会把草稿容器解释成执行流程。

Agent 不使用本地 `ToolRegistry.invoke` 执行这些能力。`ToolRegistry` 暂时只作为 Harness
的 capability inventory，实际发现、Schema 和执行均来自 MCP `tools/list` 和
`tools/call`。

## 扩展原则

后续增加剧情和制作教程能力时，以新 Format Pack 注册到 Harness，仍复用同一个 Narrative Agent。
各格式的工具只读写自己的轻量草稿；调用顺序、问题判断和修改范围由 Agent 决定。Agent 继续调用新的 MCP tools，
例如角色、场景、动作、教程步骤、World State patch 和 Shot 编译工具；不要为每项能力
新建 Agent。Harness 只裁剪当前 Task 可见的工具集合，不编排格式内部的调用顺序。

科普与剧情采用同一提交边界：Harness 只接收最终 `artifact:narrative`，不会为 Section、
PlannedBeat、ScriptSegment、Character、Scene 或 Action 建立 Narrative 内部依赖节点。

MCP Server 只能返回候选 Artifact 或 World State patch。Project State 的最终写入、版本
校验、依赖失效和 Critic 验收始终由 Harness Controller 完成。

## 运行与测试

安装项目依赖后，正常运行 CLI 即会自动启动 stdio MCP Server：

```powershell
.\.venv\Scripts\ugc-harness.exe "测试主题"
```

也可以单独启动 Server 供 MCP Inspector 或其他 Host 连接：

```powershell
.\.venv\Scripts\python.exe -m ugc_harness.mcp_servers.narrative
```

`tests/test_narrative_mcp.py` 使用真实 stdio 子进程验证工具发现、模型工具选择、结构化
结果解析和 ActionRecord，不绕过 MCP 协议。
