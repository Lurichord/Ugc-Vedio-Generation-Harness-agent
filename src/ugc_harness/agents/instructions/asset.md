# asset_agent 任务指令

你正在执行素材（asset）任务，目标是为任务范围内的每个 VisualRequirement
获取一份可用素材（first-success，不保留多候选）。

工作方式（游标式）：

1. 反复调用 `asset.acquire_requirement`：每次自动处理下一个待办的视觉需求，
   返回本次结果和剩余待办清单（pending）。
2. 如果任务是图像修复（scope.target_refs 全部为 prepared_image: 或白名单只有
   prepare 工具），改为反复调用 `asset.prepare_image`，逐个修复图像。
3. pending 清空后调用 `asset.submit_candidate` 提交候选。

约束：

- 某一次调用失败时，该条目会保留在待办里；根据错误信息决定重试或继续。
- 不要修改叙事、配音、编辑部计划或时间线。
- pending 未清空时提交会返回可修复错误。
