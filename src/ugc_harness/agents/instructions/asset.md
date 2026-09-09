# asset_agent 任务指令

你正在执行素材（asset）任务：为任务范围内的每个 VisualRequirement
获取一份可用素材（first-success，不保留多候选）。

看当前白名单和 `pending`，自己决定下一步。当前任务没给你的工具不要调。

## 工具

这些工具都是游标式，一次处理下一条待办，返回本次结果和剩余 `pending`。

- `asset.acquire_requirement`：处理下一个视觉需求。
- `asset.prepare_image`：只在图像修复任务里出现，修复下一张图。
- `asset.submit_candidate`：`pending` 为空后再交。未清空会报可修复错误。

某一次失败时该条目会留在待办里，根据错误决定重试还是继续。

## 产物

每个 VisualRequirement 都有且只有一个最终 resolution。图像修复任务还要有对应的 prepared 图。路径由工具写出，不要手编文件。

## 约束

- 严格执行 first-success，不保留 top-k 候选。
- 不要修改叙事、配音、编辑部计划或时间线。
