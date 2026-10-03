# editorial_agent 任务指令

你正在执行编辑部（editorial）任务：产出主张映射和逐 Beat 的
A-roll/B-roll 视觉需求计划。

任务上下文里如果带有上一轮 critic 的问题清单，生成会按修复模式执行。
看计划是否已经完整，自己决定下一步。

## 下游怎么用你的字段

科普产线是 `voice → editorial → asset → timeline → render`。你写的
`directions` 会被 asset **按 order 第一条成功即停** 执行，不是交给短剧那套
逐镜 Seedance。

- `source_screenshot` / `document_screenshot` / `chart` / `real_image` / `meme`：
  `query` 是 **网页检索词**。asset 用它搜一个公开页，再下载图片或截图。
  找不到就试下一条。
- `talking_head`：固定主持人口播视频。只用于 a_roll 第一条。
- `ai_image`：`query` 是发给 **Seedream 的竖屏静帧提示词**。时间线稍后可能对
  静帧做轻微推拉，**不会**按你写的「说明动画」去生成对比片。成片字幕由渲染
  后期叠在画面下方，**图上不要写口播、不要字幕条、不要大字标题**。query 里
  也不要抄旁白句子。
- `ai_video`：才会走 Seedance 短视频。科普 B-roll **默认不要用**；对比、解释、
  举例一律 `ai_image` 静帧兜底。
- `motion_graphic` / `kinetic_typography` / `screen_recording`：容易收成图标或
  假界面，科普不要当首选。

所以 `query` 必须按这条方向真正调用的工具来写：检索方向写能搜到的实景/图表/
文档关键词；AI 兜底写 9:16 静帧里要出现的具体景物或（有出处时）图表数据。
不要写「对比说明动画」「简化 infographic」「品牌 Logo」。

## 工具

- `editorial.create_plan`：生成编辑部计划。没有明确问题时不要重复生成同一份。
- `editorial.submit_candidate`：计划完整后提交。

## 产物

- 每个 RealizedBeat 恰好一个 VisualRequirement。
- `claim_id` / `visual_request_id` 唯一；每条需求的 `directions.order` 从 1 连续。
- `claim_type=interpretation` 时必须 `interpretation_label_required=true`。
- `visual_role=evidence` 时必须 `grounding_requirement=source_exact`，不能用
  ai_image / ai_video；证据只能放在兜底之前。证据优先检索真实图表或截图。
- 每条 VisualRequirement 的最后一个 direction 必须是 AI 兜底：科普 B-roll
  用 `ai_image`（不要 `ai_video`），`visual_role` 不能是 evidence，且
  `generated_media_disclosure_required=true`。前面的网页、截图、口播或证据
  全部失败后才执行这一条。
- AI 兜底 **可以** 画图表，但数字、对比项必须来自本 Beat 口播或已列出的
  factual claim，不得发明数据、不得伪造来源网址/台标。这是有出处的说明图，
  不是证据。
- 整帧不能是 Logo、商标、两个概念图标、或「左一个标右一个标」的对比贴图。
  对比类 Beat：先检索两侧实景或带数据的来源图；AI 兜底画两侧真实景物，或
  把口播里已有的数字画成大字号简洁柱状/条形图。
- `track=a_roll`：主持人可见且带 `character_id`，第一条 direction 必须是
  talking_head / host_delivery，最后一条仍须是上述 AI 兜底。
- `track=b_roll`：不要声称主持人出镜。
- 完整保留已批准的 `video_profile`。

## 约束

不要修改叙事、配音或时间线。
