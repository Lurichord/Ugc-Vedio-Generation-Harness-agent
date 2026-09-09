# references 任务指令

你正在为短剧 / 教程生成身份参考图。被镜头绑过的 cast / prop / place
各要一张竖图，写回该身份节点，不要新建节点。

看当前缺谁、已经有谁，自己决定下一步。工具错误会原样返回。

## 工具

- `references.list_identities`：列出被镜头绑定的身份，以及谁还缺图。返回 `identities` 和 `pending`（缺图的 ref 列表）。
- `references.generate_identity`：给指定身份出一张竖图并写回该节点。参数 `ref` 必须是列表里返回的身份 ref（`cast:` / `prop:` / `place:`），不要自己编 id。
- `references.submit_candidate`：范围内每条绑定身份都有可读图之后提交。服务端按已落盘的图组装产物。`pending` 非空会报可修复错误。

## 产物

每个被绑定的身份都有一张可读参考图，路径由生成工具写出。不要手写不存在的文件。

## 约束

- 已经有可读图片的身份不要重复生成。
- 不要改镜头正文，不要生成视频。
