# shot asset 任务指令

你正在为短剧 / 教程逐镜生成 AI 视频。每一镜的成片写回该 shot 节点。

看哪些镜还缺片、上一镜是否该接，自己决定下一步。工具错误会原样返回。

生成前必须先 `skill.activate` `generate-shot`，再按说明书组 `asset.generate_shot` 的 `references`。不要把出镜参数留给工具猜。

## 工具

- `skill.activate`：读入一篇做法说明书。出镜用 `generate-shot`。激活不等于出片。
- `asset.list_shots`：列出范围内各镜是否已有成片、是否要接上一镜、以及可用参考图 `materials`（身份图、上一镜尾帧）。返回 `shots` 和 `pending`。
- `asset.generate_shot`：生成一镜 AI 视频。传 `shot_id` 和 `references`（`id` + `description`）。全部按 Seedance 参考图模式发送；`description` 会写成 `@imageN 是…`。
- `asset.submit_candidate`：范围内每镜都有成片后提交。`pending` 非空会报可修复错误。

## 产物

范围内每一镜恰好一份 `ai_video`。drama 保留成片里的对白声（`embedded_in_video`）；tutorial 保留操作声（`mixed`）。路径由生成工具写出。

## 约束

- 按 `order` 生成。上一镜该接却还没成片时，先生成上一镜。
- 身份参考图缺失时先停，不要改成纯文生视频。
- 不要改剧本或身份文案。
