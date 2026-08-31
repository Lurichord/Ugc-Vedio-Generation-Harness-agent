# 01_coffee

观众开场：我想做个视频
观众要改：第三段太长，收成两句，粉水比那句留下

## 对白（真实 CLI）

**意图层**  你好，我是意图解析层。先告诉我你想做什么视频、给谁看、大概多长。
[waiting_user] topic=无
输入内容回车即可；exit / quit / 退出 结束。

**观众**  我想做个视频

**意图层**  [fields] （尚未填写）
意图：请问你这支视频具体是讲什么主题的呢？
[waiting_user] topic=无

**观众**  做一条讲手冲咖啡的抖音

**意图层**  [fields] topic=手冲咖啡  mode=explainer  duration=90s
意图：这支手冲咖啡的视频，你希望观众看完之后能做成哪一件具体的事呢？
[waiting_user] topic=手冲咖啡

**观众**  目标是让新手分清粉水比。

**意图层**  harness：项目目录 D:\桌面\ugc-vedio-generation-agent\test\intent_cli_audience\01_coffee\project\手冲咖啡
[fields] topic=手冲咖啡  mode=explainer  duration=90s  goal=让新手分清粉水比
意图：已经按当前任务书做好了脚本和口播文案，请问可以继续推进后续的制作阶段吗？
[waiting_user] topic=手冲咖啡

**观众**  第三段太长，收成两句，粉水比那句留下

**意图层**  harness：项目目录 D:\桌面\ugc-vedio-generation-agent\test\intent_cli_audience\01_coffee\project\手冲咖啡
[fields] topic=手冲咖啡  mode=explainer  duration=90s  goal=让新手分清粉水比
意图：已经按要求把讲粉水比定义的第三段修改完成，压缩成两句保留了粉水比定义的核心内容，可以继续推进后续制作了。
[waiting_user] topic=手冲咖啡

**观众**  退出

## 工具

1. `skill.activate` {"name": "clarify-brief"}
2. `skill.activate` {"name": "clarify-brief"}
3. `skill.activate` {"name": "start-project"}
4. `harness.start_project` {}
5. `skill.activate` {"name": "revise-segment"}
6. `description.list_outline` {}
7. `harness.list_graph` {"around_ref": "beat:pb03"}
8. `harness.repair` {"target_refs": ["beat:pb03"], "instruction": "把本段内容压缩为两句，保留讲粉水比定义的核心内容，删除所有冗余内容"}
