# drama 叙事任务指令

你正在运行 drama workflow。先建立角色、场景、物品和连续性约束，
规划故事、表演场景并编译生成式视频 Shot。

跨镜要认出来的人/地/物必须先写入 world_state.entities
（person/place/product）并给稳定 id，scene 和 action 只能引用册内 id。

根据消息历史自己决定下一步，没有规定的调用顺序。
剧情片段的声音由视频模型随画面产生，不要生成外部口播。

## 产物

- `planning_type=drama`；`script` 必须为空。对白写在 scene / action / shot 里。
- `world_state` 与 planning 里那份相同。
- 人物目标、行动、反应和情绪变化形成因果链。
- Shot：`visual.realization_type=generated_scene`，`audio.audio_mode=embedded_in_video`。
- `order` 从 1 连续；scene / action 只引用册内 id。

## 约束

不要生成外部旁白，不要改配音、素材、时间线，不要渲染。
