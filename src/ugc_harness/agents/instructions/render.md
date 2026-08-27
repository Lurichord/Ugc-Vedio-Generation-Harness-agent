# render_agent 任务指令

你正在执行渲染（render）任务，目标是把已批准的时间线渲染为最终 MP4 和
预览 MP4。

工作方式：

1. 调用 `render.execute` 执行渲染并探测输出。
2. 输出就绪后调用 `render.submit_candidate` 提交。

约束：

- 最终输出必须是 1080x1920、30fps，且同时包含音视频流。
- 不要修改时间线或配音，不要替换素材。
