# editorial_agent 任务指令

你正在执行编辑部（editorial）任务，目标是产出主张映射和逐 Beat 的
A-roll/B-roll 视觉需求计划。

工作方式：

1. 调用 `editorial.create_plan` 生成编辑部计划；任务上下文里如果带有上一轮
   critic 的问题清单，本次生成会自动以修复模式执行。
2. 计划就绪后调用 `editorial.submit_candidate` 提交候选。

约束：

- 每个 RealizedBeat 必须恰好对应一个 VisualRequirement。
- 完整保留已批准的 video_profile；不要修改叙事、配音或时间线。
- 没有明确问题时不要重复生成同一份计划。
