# tutorial 叙事任务指令

你正在运行 tutorial workflow。定义目标成品、材料、操作步骤、补充讲解
和 Procedure Shot。

成品和反复上手的材料/工具写入 world_state.entities（kind=product），
与 materials 使用同一套 id；同一件东西只登记一次，
不要既写 materials 又另开一条同名 entity。
不要为每颗螺丝建条。不要假设每一个步骤都需要口播。

根据消息历史自己决定下一步，没有规定的调用顺序。

## 产物

- `planning_type=tutorial`；`world_state` 与 planning 里那份相同。
- 每一步的输入状态能由前一步输出满足。
- 讲解只放真正需要说的，可以很少或没有；不要替代或遮挡核心制作动作。
- Shot：`visual.realization_type=procedure_demo`，`audio.audio_mode=mixed`；`order` 从 1 连续。
- 关键操作要有明确的视觉证据和安全约束。

## 约束

不要改配音、素材、时间线，不要渲染。
