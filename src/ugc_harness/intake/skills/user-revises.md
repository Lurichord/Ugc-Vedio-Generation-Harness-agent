---
name: user-revises
description: 用户改口换主题、时长、体裁或目标。对照更新后的 brief，确认整支重做还是只改一段。
---

先看这句用户话：字面上的字段正则可能已经写入。点头、选第几个、或「算了改成…」这种要对白才能理解的改口，本拍先 `brief.update` 覆盖，再往下问。不要凭空猜测没说过的字段。

## 先分清改的是哪一类

- 改诉求（主题、目标、观众、时长、平台、体裁）：走本 skill，不要去改镜头。
- 改已经做出来的某一段说法（「第一段太短」「这句换掉」）：不要在这里修，告诉自己下一拍走 `revise-segment`。
- 只改参考图、某一镜生成效果或音色：下一拍走 `revise-media`。
- 要改已经做出来的规格，但还没点名哪一段（「这段剧情不对」）：下一拍走 `inspect-graph`。
- 两件事缠在一起：只先确认诉求，正文先不动。

演绎对白在成片里，没有独立配音。教程讲解有独立配音。科普才走 editorial 前的 voice。

对照 `working_intent`。brief 和已经承诺的 intent 打架时，只问用户以谁为准，不要默默覆盖，也不要直接 `start_project` 或 `repair`。

## 已经有项目时怎么问

制作已经跑过，改口通常意味着旧脚本不再匹配。用一句人话点出**变了的那一项**，再问范围：

- 主题/目标变了：整支按新任务书重做，还是只改对不上的那一段。
- 只改时长或平台、主题没变：说清楚这会影响节奏和篇幅，问要不要按新约束重做叙事。

不要自己改正文，不要默默再 `start_project`。用户明确说整支重做，再走 `start-project`（若项目已在跑，先说明不能重复开工，问是否按新 brief 修叙事）。用户明确说只改某一段说法，下一拍再 `revise-segment`。只改图或成片或音色，下一拍再 `revise-media`。还没点名哪一段、要改规格，下一拍再 `inspect-graph`。

## 还没开工时

`brief.update` 之后看 JSON 够不够具体。太薄就按 `clarify-brief` 的标准只补一项。主题改成动手做、或改成讲道理时，同步把 `production_mode` 改成 `tutorial` / `explainer` / `drama`。不要为改口而催开工。

## 输出格式

改口后先写入（若正则还没写上）：

```json
{
  "tool": "brief.update",
  "arguments": {"topic": "端午节包粽子"},
  "message": "",
  "done": false
}
```

再对用户确认一句，不要直接开工或 repair：

```json
{
  "tool": null,
  "arguments": {},
  "message": "主题改成了教新手包粽子。按新主题整支重做，还是只改某一段？",
  "done": false
}
```

`message` 要点出变化，不要复述整个 brief。brief 和 intent 打架时，只问以谁为准。
