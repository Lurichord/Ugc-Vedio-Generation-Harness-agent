# UGC Video Agent

基于 LangChain `create_agent` 的 UGC 视频 Agent。

- AgentState 与 Checkpointer 保存对话和工具过程。
- ProjectState 保存视频内容和媒体事实。
- Agent 使用 LangChain 官方 Middleware 限制模型调用次数。
- Composition Skill 将在后续实现。
