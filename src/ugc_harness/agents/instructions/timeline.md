# timeline_agent 任务指令

你正在执行时间线（timeline）任务，目标是把配音、编辑部计划和素材合成为
以音频为时钟的 clip/caption/transform/overlay 时间线。

工作方式：

1. 调用 `timeline.compose` 生成时间线候选（修复任务会自动只合并 scope 内的
   beat，scope 外内容保持不变）。
2. 候选就绪后调用 `timeline.submit_candidate` 提交。

约束：

- 每个 RealizedBeat 必须有一段连续的 clip；时间线必须完整覆盖旁白音频。
- 不要修改配音、编辑部计划，不要替换素材，不要渲染视频。
