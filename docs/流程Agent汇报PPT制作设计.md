# 流程 Agent 汇报 PPT 制作设计

## 1. 汇报定位

### 汇报主题

**从内容规划到成片：UGC 视频生成流程 Agent 的实现**

### 与上次汇报的关系

上次汇报重点是 Harness 的公共执行框架，包括任务信封、状态提交、依赖图、Critic 和局部修复。本次不再重复讲框架本身，而是回答三个更具体的问题：

1. 每个流程 Agent 实际接收什么输入、执行哪些步骤、输出什么产物？
2. Agent 如何把上游抽象结果转换成下游可消费的、按 Beat 对齐的产物？
3. 文本、语音、图片、视频、时间线和渲染模型如何在一条工程链路中协作？

### 沟通目标

汇报结束时，听众应当理解：**这套系统不是让一个大模型一次性生成视频，而是让六个职责单一的 Agent 围绕同一个 Beat 时钟依次工作，并在每个阶段形成可验收、可回退、可局部替换的结构化产物。**

### 建议时长

- 标准版本：18～22 分钟
- 重点讲解：Narrative、Voice、Editorial、Asset、Timeline、Render 六个 Agent
- 建议页数：17 页正文 + 2 页附录

---

## 2. 整体视觉设计

### 风格关键词

浅色背景、简约、技术感、留白充足、结构清晰、避免“后台管理系统式卡片堆叠”。

### 画布与网格

- 比例：16:9
- 背景：暖白 `#F7F8FA`
- 页面左右边距：0.75 英寸
- 标题区高度：约 15%
- 主内容区：约 75%
- 页脚区：约 10%
- 默认使用左对齐；流程图节点沿单一基线排列

### 色彩系统

| 用途 | 颜色 | 说明 |
|---|---|---|
| 主文字 | `#172033` | 深蓝黑，替代纯黑 |
| 次级文字 | `#5E687A` | 说明、注释 |
| 主强调色 | `#356AE6` | 当前 Agent、主流程、关键结论 |
| 成功/通过 | `#2E9B72` | Critic 通过、已提交 |
| 修复/警告 | `#E29A35` | 重试、局部修复、降级 |
| 错误/阻断 | `#D85C5C` | 失败、阻断条件 |
| 分隔线 | `#DDE2EA` | 辅助线、流程连接线 |
| 浅蓝底 | `#EAF0FF` | 关键概念底色，少量使用 |

### 字体建议

- 中文：思源黑体 / 阿里巴巴普惠体 / 微软雅黑
- 英文和数字：Inter / Aptos
- 封面标题：30～34 pt（PowerPoint 实际制作时建议 50 pt 以上）
- 页标题：24～28 pt（实际制作时建议 35 pt 以上）
- 二级标题：18～20 pt（实际制作时建议 24 pt 以上）
- 正文：15～18 pt（实际制作时不低于 16 pt）
- 代码与字段：JetBrains Mono / Consolas

### 图形语言

- Agent：圆角矩形，白底，1.25 pt 描边
- 产物：窄条形文档块，左上角折页符号
- 工具调用：细线连接的小标签，不绘制复杂图标
- Critic：独立圆形或六边形节点，使用绿色描边
- 失败路径：橙色虚线回箭头
- Beat：统一使用 `B01 / B02 / B03` 小型标签
- 不使用渐变、玻璃拟态、3D 图标和大面积阴影

### 页面统一元素

- 左上角：页标题
- 右上角：所属阶段，例如 `AGENT 03 · EDITORIAL`
- 左下角：项目名 `UGC Video Generation Agent`
- 右下角：页码
- 正文页只突出一个核心结论，避免每页超过 6 个信息块

---

## 3. 故事线与页面结构

### 第一部分：从 Harness 进入 Agent

先用两页完成上下文切换：上次解释“系统如何管住 Agent”，本次解释“Agent 具体如何生产视频”。

### 第二部分：六个流程 Agent

按真实执行顺序展开。每个 Agent 均回答：

- 输入是什么
- 内部执行流程是什么
- 调用了什么能力或模型
- 输出什么 Beat 级产物
- Critic 检查什么
- 失败后影响哪些下游节点

### 第三部分：协作与局部修复

用一页说明所有 Agent 如何围绕 Beat、音频时钟和依赖图协作，再用一页给出当前完成度与工程问题。

---

## 4. 逐页制作方案

## 第 1 页｜封面

### 标题

**从内容规划到成片**

### 副标题

UGC 视频生成流程 Agent 的设计与实现

### 页面内容

底部小字：

`Narrative → Voice → Editorial → Asset → Timeline → Render`

### 版式

左侧 60% 放标题；右侧以一条细蓝线串联六个小节点。背景保持暖白，仅在右下角放一个淡蓝色竖屏视频轮廓作为装饰。

### 本页要传达的结论

本次汇报从公共 Harness 下沉到各流程 Agent 的真实实现。

---

## 第 2 页｜这次汇报关注 Agent 如何把抽象内容变成成片

### 页面主文案

上次回答：**如何约束、验收和修复 Agent？**

这次回答：**每个 Agent 到底做了什么？**

### 版式

左右二分：

- 左侧用浅灰细线框展示上次内容：Task、State、Graph、Critic
- 右侧用主蓝色展示本次内容：六个流程 Agent 及产物

中间使用一条向右箭头，并放置一句承上启下的话：

> Harness 提供可控执行边界，流程 Agent 在边界内完成领域生产。

### 讲解重点

避免再次详细介绍 Harness，只强调本次仍沿用相同的任务、工具白名单、独立质检和状态提交机制。

---

## 第 3 页｜六个 Agent 围绕同一条 Beat 链路工作

### 标题结论

**视频不是一次生成，而是六次语义逐步落地**

### 主流程

```text
CreativeBrief
    ↓
Narrative Agent ── Section / PlannedBeat / ScriptSegment
    ↓
Voice Agent ────── WAV / WordAlignment / RealizedBeat
    ↓
Editorial Agent ─ Claim / VisualRequirement
    ↓
Asset Agent ────── AssetCard / VisualResolution
    ↓
Timeline Agent ─── Clip / Caption / Transform / Overlay
    ↓
Render Agent ───── final.mp4 / preview.mp4
```

### 版式

横向单线流程。每个 Agent 下方只放 2～3 个核心产物，避免把所有字段塞入总览页。用 `Beat ID` 细线贯穿 Voice 之后的所有阶段，表示后续全部按 Beat 对齐。

### 讲解重点

- Narrative 先产生“计划 Beat”
- Voice 用真实音频重新实现为“时间 Beat”
- 后续画面、素材和时间线都读取 RealizedBeat，而不是依赖预估时长

---

## 第 4 页｜所有 Agent 使用同一种可控执行骨架

### 标题结论

**Agent 负责提出候选结果，Critic 验收，Controller 决定是否提交**

### 流程图

```text
TaskEnvelope
  ├─ scope / beat_ids / target_refs
  ├─ allowed_tools
  ├─ input_hash / state_version
  └─ budget
          ↓
Domain Agent → 白名单工具 → Candidate Artifact
          ↓
Independent Critic
     ┌────┴────┐
   passed    rejected
     ↓           ↓
  commit      revise / repair
```

### 版式

中央纵向流程，不使用六张卡片。Agent 节点用蓝色，Critic 用绿色，Controller 提交用深色底。右侧用三行注释强调：

- Agent 不能直接改 ProjectState
- Agent 不能调用未授权工具
- 旧版本结果不能覆盖新状态

### 讲解重点

这一页是唯一一页公共骨架，后面不再重复 Harness 细节。

---

## 第 5 页｜Narrative Agent：先规划认知推进，再写口播

### 标题结论

**Narrative 将一个主题拆成可验证的内容世界、Beat 和口播段落**

### 输入

- `CreativeBrief`
- 主题、平台、受众、目标时长、语气、创作者人设
- `video_profile`：`auto / a_roll / b_roll / ab_roll`

### 内部实现流程

```text
CreativeBrief
   ↓ narrative.generate_plan
WorldState + VideoProfile + Section[] + PlannedBeat[]
   ↓ 本地计划检查
Close 是否回扣 Hook？用户指定的画面模式是否被保留？
   ↓ narrative.generate_script
ScriptSegment[] + DeliveryHint
   ↓ 本地脚本检查
Beat 覆盖、强调词、估算时长
```

### 关键实现细节

- 规划和写稿拆成两个独立工具调用，而不是一次生成全部内容
- `WorldState` 保存实体、主张、因果关系、未知问题与叙事边界
- 每个 `ScriptSegment` 显式关联一个 `planned_beat_id`
- 用户指定 `b_roll` 时，模型不得自行改回其他画面模式
- 计划或脚本不合格时，在任务预算内带问题重新生成

### 输出

`PlanningArtifact`、`ScriptArtifact`、`NarrativeArtifact`

### 版式

左侧为两段式生成流程，右侧放一个简化的 JSON 字段树。突出 `planned_beat_id` 和 `video_profile.resolved`。

---

## 第 6 页｜Narrative 的难点不是写文案，而是保证结构可被下游消费

### 标题结论

**结构化约束把“好文案”变成“可继续生产的文案”**

### 三个检查层次

1. **Schema 层**：模型输出必须通过 Pydantic 类型校验
2. **领域规则层**：Hook / Body / Close 顺序、Close 回扣、Beat 覆盖
3. **独立 Critic 层**：重新计算时长、覆盖率、核心主张和结构问题

### 推荐示例

使用“端午节 B-roll”作为示例，在页面下方展示一条简化映射：

```text
pb01 端午节不只是吃粽子
  └─ ss01「很多人提到端午节，第一反应是粽子……」

pb02 习俗与避疫传统相关
  └─ ss02「赛龙舟、佩香囊、挂艾草……」
```

### 工程说明

当前大体量结构化请求是耗时热点：SDK 超时与业务修复叠加时，等待可能被放大。后续应补充阶段日志、较短超时和更细粒度生成。

### 版式

上半区三层检查，采用由浅到深的水平分层；下半区只放两条 Beat 示例和一个橙色工程提示。

---

## 第 7 页｜Voice Agent：用真实语音重新定义整条视频的时间

### 标题结论

**从这一阶段开始，视频时长由真实音频决定，而不是由文本估算**

### 输入

`NarrativeArtifact` 中的 `ScriptSegment[]`、人物声音画像与 delivery hints。

### 内部实现流程

```text
ScriptSegment[]
   ↓ voice.create_plan
语速 / 能量 / 段前停顿 / 段后停顿
   ↓ Seed-TTS 分段合成
audio/segments/vsXX.wav
   ↓ WAV 检查与拼接
audio/narration.wav
   ↓ 原生逐词时间戳；缺失时按时长比例回退
WordAlignment
   ↓ 按实际音频区间聚合
RealizedBeat[]
```

### 关键实现细节

- 每个脚本段独立合成，便于失败后局部重试
- 记录服务端 `request_id`、`log_id`，便于追踪供应商调用
- 每个音频段保留真实 `start_ms / end_ms / duration_ms`
- 将字级时间戳平移到全局音频时钟
- 按音频段重建 `RealizedBeat`，生成后续统一时间基准
- A-roll 模式下，TTS speaker 必须与 WorldState 中人物性别和年龄风格一致

### 输出

`VoicePlan`、`TimedAudio`、`WordAlignment`、`RealizedBeat[]`、`VoiceArtifact`

### 版式

用一条音频波形作为页面主视觉，波形上方标 ScriptSegment，下方标 RealizedBeat 与逐词时间戳。不要使用真实复杂波形，可用简化线条示意。

---

## 第 8 页｜Editorial Agent：把“说什么”翻译成“每个 Beat 看什么”

### 标题结论

**Editorial 不获取素材，只定义逐 Beat 的画面需求和探索顺序**

### 输入

- `NarrativeArtifact`
- `VoiceArtifact.realized_beats`
- 已确认的 `video_profile`

### 内部实现流程

```text
RealizedBeat + narration
   ↓ 主张抽取
Claim：factual / interpretation / opinion / rhetoric
   ↓ 视觉功能判断
evidence / explanation / context / emotion
   ↓ 轨道选择
a_roll / b_roll
   ↓ 生成有序探索方向
ExplorationDirection[]
   ↓ 固定 selection_policy
first_success
```

### 关键实现细节

- 每个 RealizedBeat 必须且只能对应一个 `VisualRequirement`
- `VisualRequirement` 增加具体 `asset_type`，例如网页截图、真实图片、AI 图片、AI 视频等
- factual claim 的 evidence 方向只能覆盖事实型主张
- Editorial 不下载网页、不生成图片、不调用视频模型
- 用户指定 B-roll 时，所有 Beat 的视觉轨道必须保持 B-roll
- Critic 拒绝后带诊断重新规划；全量规划最多保留第三个可用候选

### 输出

`Claim[]`、`VisualRequirement[]`、`EditorialPlan`、`EditorialArtifact`

### 版式

左侧放一条 Beat 口播，右侧将其展开成 Claim、视觉角色、素材类型、探索方向四层结构。用细线表达“翻译”过程。

---

## 第 9 页｜Asset Agent：按 first-success 顺序获取一份可用素材

### 标题结论

**Asset 的目标不是收集最多候选，而是尽快确定每个 Beat 的唯一素材**

### 决策流程

```text
VisualRequirement
   ↓ 按 order 遍历 directions
Direction 1 → 获取 → 可用？── 是 → 选中并停止
                         └ 否
Direction 2 → 获取 → 可用？── 是 → 选中并停止
                         └ 否
AI 生成兜底 → 可用？──── 是 → 选中
                         └ 否 → unresolved
```

### 关键实现细节

- 不保留 top-k，不在成功后继续生成，控制成本和决策复杂度
- 每一次尝试都写入 `DirectionAttempt`
- 最终只输出一条 `VisualResolution`
- 每个 AssetCard 记录本地路径、MIME、SHA-256、来源或生成模型
- AI 生成素材带 `generated_media_disclosure_required`
- 局部修复任务只重新获取被点名的 `visual_request_id`

### 输出

`AssetCard[]`、`VisualResolution[]`、`AssetArtifact`

### 版式

主视觉采用单向决策树。成功分支统一汇入一个蓝色 `Selected Asset` 节点；失败路径使用橙色细线。

---

## 第 10 页｜Asset Provider：网页素材与 AI 生成走两条独立路径

### 标题结论

**Provider 路由让“真实来源素材”和“生成式兜底”保持不同的审查逻辑**

### 左侧：Web Asset 路径

```text
方舟 Responses API + web_search
  → 返回一个 URL / title / publisher
  → 校验公开 URL
  → 下载 og:image 或用 Edge 截图
  → 检查登录墙、验证码、遮罩和可读性
  → 写入 SourceTrace
```

支持类型：`source_screenshot`、`document_screenshot`、`chart`、`real_image`、`meme`。

### 右侧：Generated Asset 路径

```text
ai_image / motion_graphic
  → Seedream 5.0

ai_video
  → Seedance 2.0 异步任务
  → 保存 task_id 与进度
  → 轮询完成并下载 MP4
```

### 关键边界

- Web Search 只负责找到来源，不把搜索回答当作事实证明
- 下载后仍进行独立可用性检查
- 真实素材失败后才进入下一方向或生成式兜底
- Seedance 参考图被接口拒绝时，可降级为文本生成视频

### 版式

页面中央用一条竖线分隔两条路径。左侧使用蓝灰色，右侧使用淡紫蓝色；底部汇入同一个 `AssetCard`。

---

## 第 11 页｜Asset Critic：素材“生成成功”不等于“可以剪进视频”

### 标题结论

**素材阶段同时检查覆盖、文件、画面可用性和人物连续性**

### 主要检查项

- 每个 VisualRequirement 是否有且只有一个 resolution
- 本地文件是否存在、非空、哈希是否一致
- first-success 是否被违反
- 图片分辨率、文字可读性、主体大小与构图是否适合 9:16
- 网页截图是否被登录、注册、验证码或认证界面遮挡
- A-roll 是否复用统一人物标识和连续性分组
- talking-head 是否具备可用于竖屏视频生成的人物参考图

### 两种修复策略

```text
可裁剪修复
低分辨率 / 主体过小 / 文字不可读
  → asset.prepare_image
  → contain / focus_crop / cover

不可裁剪修复
素材不可访问 / 类型错误 / 视频失效
  → 只重新执行对应 visual_request_id
```

### 版式

左侧为检查清单，右侧为“准备图片”和“重新获取”两条分叉路径。突出“局部修复不会重做其他已通过 Beat”。

---

## 第 12 页｜Timeline Agent：以音频时钟为主轴拼装所有视觉层

### 标题结论

**Timeline 不重新决定内容，只把已批准产物精确放到时间上**

### 输入

`VoiceArtifact + EditorialArtifact + AssetArtifact`

### 内部实现流程

```text
RealizedBeat.start_ms / end_ms
   ↓ 为每个 Beat 找到 VisualResolution 与 AssetCard
TimelineClip
   ├─ playback_path / modality
   ├─ transition_in
   └─ timeline_start_ms / timeline_end_ms

WordAlignment → CaptionCue[]
Asset 类型与视觉角色 → VisualTransform[]
来源/AI/观点属性 → OverlayCue[]
```

### 关键实现细节

- 每个 RealizedBeat 对应一个 Clip，顺序和覆盖必须完全一致
- 图片根据视觉角色使用 `slow_pan` 或 `subtle_push` 等轻动效
- 字幕按标点、字数或约 2.2 秒时长切分
- 来源素材自动生成来源标注
- AI 图片、AI 视频和屏幕动画自动生成“AI 生成画面”标注
- 观点或推断内容自动增加解释性标签
- 对需要交互演示的屏幕素材，可生成 Seedance 动态衍生视频

### 输出

`TimelinePlan`、`CaptionCue[]`、`VisualTransform[]`、`OverlayCue[]`、`TimelineArtifact`

### 版式

使用四轨时间线作为主视觉：声音、字幕、画面素材、展示方式。播放头落在某一 Beat 上，展示四轨同时对齐。

---

## 第 13 页｜Timeline Critic：时间线必须连续覆盖整段旁白

### 标题结论

**时间线质量的核心是“无空洞、不错序、可播放”**

### 检查规则

```text
首个 Clip.start_ms = 0
最后 Clip.end_ms = narration.duration_ms
相邻 Clip：left.end_ms = right.start_ms
Clip beat_id 顺序 = RealizedBeat 顺序
每个 Clip 恰好一个 VisualTransform
每个 Beat 至少一个 CaptionCue
所有 playback_path 文件存在且非空
```

### 失败后的处理

- 问题落到具体 `timeline_clip:<beat_id>` 或 timeline artifact
- 修复操作为 `recompose_beat`
- 只使最终渲染节点失效，不修改语音、编辑规划或素材

### 版式

上方放一条连续时间线，下方用三个放大框展示“空洞”“重叠”“素材缺失”三种错误。通过状态用绿色细线表示。

---

## 第 14 页｜Render Agent：把结构化时间线确定性地渲染为 MP4

### 标题结论

**最后一步不再调用大模型，而是执行可复现的媒体渲染**

### 内部实现流程

```text
TimelineArtifact + VoiceArtifact
  → 毫秒换算为 30fps 帧号
  → 构建 RenderComposition
  → 复制任务所需媒体到隔离 job 目录
  → Remotion + Edge 渲染 final.mp4
  → FFmpeg 转码 preview.mp4
  → FFprobe 读取媒体属性
  → 清理临时 job 目录
```

### 关键实现细节

- 最终视频：1080 × 1920、30 fps
- 预览视频：540 × 960
- 视频编码：H.264；音频编码：AAC
- 根据 Timeline 中的 fit mode、motion preset、transition、caption 和 overlay 渲染
- 渲染前检查所有媒体和旁白文件是否存在
- 输出记录 SHA-256、文件大小、分辨率、帧率、时长和音视频流信息

### Critic 验收

- final 与 preview 是否存在
- 时长与旁白误差是否小于等于一帧
- 分辨率、帧率、编码和音视频流是否正确
- 已批准 Timeline 是否覆盖完整音频

### 版式

左侧为 RenderComposition 字段树，右侧为竖屏 final 和小尺寸 preview 两个视频轮廓，中间用 Remotion 和 FFmpeg 两个小标签连接。

---

## 第 15 页｜模型不是一个中心，而是被分配给最适合的流程能力

### 标题结论

**当前所有生成能力统一迁移到火山引擎，但每类媒体使用不同模型**

### 模型与职责

| 能力 | 当前配置 | 使用阶段 |
|---|---|---|
| 结构化文本 | `doubao-seed-2-0-lite-260215` | Narrative、Editorial、素材审查与搜索编排 |
| 语音 | `seed-tts-2.0` | Voice |
| 图片 | `doubao-seedream-5-0-260128` | Asset 图片兜底、人物参考图 |
| 视频 | `doubao-seedance-2-0-260128` | Asset 视频、Timeline 动态衍生 |
| 联网搜索 | 方舟 Responses `web_search` | Asset Web Provider |
| 确定性渲染 | Remotion + FFmpeg + FFprobe | Render |

### 讲解重点

- `openai` Python 包只作为方舟兼容 API 的客户端，不访问 OpenAI 服务
- 生成式模型负责内容或媒体候选；最终时序与编码由确定性工具完成
- 所有模型输出进入结构化产物后才允许被下一 Agent 消费

### 版式

不用表格卡片墙。建议用六条横向能力带，左侧为能力，中央为模型，右侧为对应 Agent。

---

## 第 16 页｜局部修改沿依赖图传播，而不是整条流水线重跑

### 标题结论

**用户可以点名一个 Beat 的一个产物，系统只重做受影响的下游节点**

### 示例

用户反馈：

> B07 的画面不符合“龙舟竞渡”的氛围，请换成真实龙舟比赛视频。

### 修复路径

```text
visual_requirement:vr07
        ↓ 点名修改
Asset Agent 仅重新获取 vr07
        ↓
asset:asset_vr07 更新
        ↓
timeline_clip:b07 标记 stale 并重新组合
        ↓
rendered_media:final 重新渲染

其余 Beat 的 AssetCard 保持不变
```

### 关键机制

- `target_ref` 精确定位产物
- `dependency_snapshot` 防止运行期间依赖被替换
- `input_hash` 和 `state_version` 拒绝旧结果覆盖新状态
- `select_repair_commits` 只提交修复范围内节点
- 前端按阶段和 Beat 展示产物，用户确认后才能继续下一阶段

### 版式

采用一条纵向依赖链，B07 使用主蓝色，未受影响的 B06/B08 使用浅灰并放置“保持不变”。这是全篇唯一一张依赖图细节页。

---

## 第 17 页｜当前系统已经形成完整闭环，但仍有三个工程重点

### 标题结论

**主流程已贯通，下一步重点是可观测性、稳定性和素材质量**

### 已完成

- 六个流程 Agent 及独立 Critic
- 逐 Beat 阶段产物与人工确认
- Task 按阶段和时间展示
- 声音、文本、素材和展示方式统一时间线预览
- 火山引擎文本、语音、图片和视频模型接入
- 依赖图驱动的局部修改与下游失效传播

### 当前工程重点

1. **请求可观测性**：为大结构化模型请求补充实时日志、单次超时和明确重试次数
2. **外部能力稳定性**：对 web search、图片生成和异步视频任务增加状态展示与错误分类
3. **素材可用性**：加强画面语义检查、竖屏适配和真实素材来源质量

### 收束文案

> 这套系统的核心价值，不只是“能生成一条视频”，而是每个阶段都能看见、确认、定位和重做。

### 版式

左侧放已完成的单条流程线，右侧放三个下一步重点。底部用一行主蓝色收束文案结束，不制作单独“谢谢”页。

---

## 5. 附录页设计

## 附录 A｜Agent 输入输出速查

| Agent | 主要输入 | 核心工具 | 主要输出 | 下游阶段 |
|---|---|---|---|---|
| Narrative | CreativeBrief | generate_plan、generate_script | Planning、Script | Voice |
| Voice | NarrativeArtifact | create_plan、synthesize_narration | Audio、Alignment、RealizedBeat | Editorial |
| Editorial | Narrative + Voice | create_plan | Claim、VisualRequirement | Asset |
| Asset | Editorial + Voice | acquire_requirement、prepare_image | AssetCard、Resolution | Timeline |
| Timeline | Voice + Editorial + Asset | compose | Clip、Caption、Transform、Overlay | Render |
| Render | Voice + Timeline | render.execute | final.mp4、preview.mp4 | Complete |

版式建议：使用一张横向表格，作为答疑时的快速索引，不在正文详细讲解。

## 附录 B｜核心产物文件

```text
narrative_artifact.json
voice_artifact.json
editorial_artifact.json
asset_artifact.json
timeline_artifact.json
render_artifact.json

audio/narration.wav
assets/**
video/final.mp4
video/preview.mp4
harness/project_state.json
```

强调：聚合 artifact 面向程序消费；拆分编号 JSON 面向阶段查看和人工审核。

---

## 6. 动画与演示建议

### 动画原则

- 只使用“出现”和“淡化”两种动画
- 流程图按执行顺序逐节点出现
- 不使用飞入、旋转、缩放弹跳
- 每页最多 3 次点击，避免演示被动画打断

### 推荐现场演示顺序

如果需要结合网页演示，可在第 12 页后切换系统：

1. 打开“端午节讲解”项目
2. 展示 Editorial 阶段某一个 Beat 的 `VisualRequirement`
3. 切换 Asset 阶段，展示该 Beat 的图片或视频素材
4. 进入统一时间线，点击画面素材并预览视频
5. 返回 PPT 第 16 页，说明局部反馈如何触发依赖图修复

### 截图要求

- 截图只保留与当前结论有关的区域
- 使用浏览器浅色主题
- 隐藏本地路径、API Key、个人信息和无关控制台日志
- 视频画面保持完整 9:16 比例，使用 `contain`，不得裁切主体
- 同一张截图不要在多页重复使用

---

## 7. 制作时的内容控制

### 必须讲清楚

- PlannedBeat 与 RealizedBeat 的区别
- Editorial 只规划画面，不获取素材
- Asset 的 first-success 与生成式兜底
- Timeline 由真实音频时钟驱动
- Render 是确定性工程过程，不是再次让模型“生成成片”
- Agent、Critic、Controller 的职责边界
- 用户反馈如何定位到具体 Beat 和具体产物

### 可以弱化

- Pydantic 每一个字段定义
- DependencyGraph 的全部节点类型
- 所有 CLI 参数
- 每个模型的计费与供应商控制台配置
- 过多异常堆栈和历史调试过程

### 禁止出现

- API Key、Token 或 `.env` 实际内容
- 未验证的性能指标、成功率或成本数字
- 把方舟兼容 SDK 描述成 OpenAI 服务
- 把 Web Search 结果描述成已经完成事实核验
- 把尚未完整生成的 B-roll 项目描述成已成功成片

---

## 8. 代码依据与制作取材位置

以下文件用于核对 PPT 内容，制作时不必全部展示：

- `src/ugc_harness/agents/narrative_agent/agent.py`
- `src/ugc_harness/harness/controller.py`
- `src/ugc_harness/agents/voice_agent/agent.py`
- `src/ugc_harness/agents/voice_agent/capabilities.py`
- `src/ugc_harness/agents/voice_agent/tts.py`
- `src/ugc_harness/agents/editorial_agent/agent.py`
- `src/ugc_harness/harness/editorial_controller.py`
- `src/ugc_harness/agents/asset_agent/agent.py`
- `src/ugc_harness/agents/asset_agent/capabilities.py`
- `src/ugc_harness/agents/asset_agent/providers.py`
- `src/ugc_harness/harness/asset_controller.py`
- `src/ugc_harness/agents/timeline_agent/capabilities.py`
- `src/ugc_harness/harness/timeline_controller.py`
- `src/ugc_harness/agents/render_agent/capabilities.py`
- `src/ugc_harness/harness/render_controller.py`
- `src/ugc_harness/evaluators/*_critic.py`
- `src/ugc_harness/shared/settings.py`
- `src/ugc_harness/shared/image_generation.py`
- `src/ugc_harness/shared/video_generation.py`

---

## 9. 一句话汇报主线

> Harness 解决“如何让 Agent 可控”，六个流程 Agent 解决“如何把一个主题逐步变成可审核、可定位、可局部重做的最终视频”。
