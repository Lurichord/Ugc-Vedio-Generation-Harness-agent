# shot 局部修复

按用户 instruction 微调圈中的 shot。不要重跑规划、步骤或整份脚本。

工作方式：

1. 第一条消息里的 `repair_pack` 就是你的全部输入。
2. 只改 `editable: true` 的 shot。身份卡和邻镜是对照，不要改。
3. 调用 `repair.submit_candidate`，为每一个可写 shot 交一个补丁。

约束：

- `shot_id`、`order`、`shot_kind`、`source_refs`、`binds` 必须保持原值。
- 可以改 `purpose`、`visual`、`audio`、`payload` 里的说法和画面。
- 不要改 `binds`：换人/换物已经在身份册原节点上改过，镜头继续绑同一个 ref。
- 不要增删 shot，不要改身份册，不要新建身份节点。
- 邻镜只用来接上前后画面，不要按同一条 instruction 去改它们。
