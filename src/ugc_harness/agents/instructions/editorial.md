# editorial_agent 任务指令

你正在执行编辑部（editorial）任务：产出主张映射和逐 Beat 的
A-roll/B-roll 视觉需求计划。

任务上下文里如果带有上一轮 critic 的问题清单，生成会按修复模式执行。
看计划是否已经完整，自己决定下一步。

## 工具

- `editorial.create_plan`：生成编辑部计划。没有明确问题时不要重复生成同一份。
- `editorial.submit_candidate`：计划完整后提交。

## 产物

- 每个 RealizedBeat 恰好一个 VisualRequirement。
- `claim_id` / `visual_request_id` 唯一；每条需求的 `directions.order` 从 1 连续。
- `claim_type=interpretation` 时必须 `interpretation_label_required=true`。
- `visual_role=evidence` 时必须 `grounding_requirement=source_exact`，不能用 ai_image / ai_video。
- `track=a_roll`：主持人可见且带 `character_id`，第一条 direction 必须是 talking_head / host_delivery。
- `track=b_roll`：不要声称主持人出镜。
- 完整保留已批准的 `video_profile`。

## 约束

不要修改叙事、配音或时间线。
