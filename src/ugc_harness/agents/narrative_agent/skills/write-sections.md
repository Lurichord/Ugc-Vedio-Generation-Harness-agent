---
name: write-sections
agent: narrative_agent
description: 讲解第一层。没有前置。写出 world_state、video_profile 和 Hook/Body/Close。
---

只在讲解首跑第一层读这篇。激活不等于落盘。Brief 在上下文里，不要改 Brief 已定的 thesis 和时长。

## 前置

无。若叙事产物里已经有三节，不要重写，去 `write-beats`。

## 步骤

1. `skill.activate`，`arguments`：`{"name": "write-sections"}`。
2. `narrative.write_sections`，带完整 `sections`。工具同时写入身份册。
3. 然后激活 `write-beats`。

## 身份册

写入 `world_state.entities`。只登记镜头上必须认成同一个的人、地点、物品：Brief 点过名、会跨至少两镜，或该镜主体。

- 人 `person`、地 `place`、物 `product`；`entity_id` 稳定。
- 路人、一次性名词、机构名、口播举例的品牌、抽象概念不要进册。概念用 `kind=concept`，不进 `cast:` / `place:` / `prop:`。
- 讲解通常 0–1 个出镜主持人（写在 `aroll_character`）加 1–2 个主题产品。

## world_state

描述视频内容世界，不描述模型或工具环境。

- `topic_frame`：本片怎么框主题。
- `entities`：至少一条。
- `claims`：至少一条。Brief 没直接给出、却会按事实讲述的主张，`epistemic_status=to_verify` 且 `evidence_required=true`。观点和推断不能伪装成已知事实。
- `causal_links`、`open_questions`、`narrative_boundaries`：写清因果、待答问题和不能越过的边界。
- `one_sentence_thesis` 跟 Brief。

`epistemic_status`：`given_by_brief` / `to_verify` / `interpretation` / `hypothesis`。

## video_profile

Brief.`video_profile` 不是 `auto` 时必须原样采用，`selection_source=user`，`resolved` 等于 requested。

为 `auto` 时在 `a_roll` / `b_roll` / `ab_roll` 中选，`selection_source=ai`。

- `a_roll`：人物口播画面。
- `b_roll`：画外音配补充画面；`aroll_character` 必须为 `null`。
- `ab_roll`：一致人物口播为主，按需插入证据画面；出镜占比建议 0.55–0.75。

`a_roll` / `ab_roll` 必须同时写：

- `aroll_character` 字段是 `character_id`，**不要写 `entity_id`**
- `video_profile.character_id` 与 `world_state.aroll_character.character_id` 同一字符串
- `video_profile.character_description` 与 `aroll_character.visual_description` **逐字相同**
- `aroll_character.voice_profile`：`gender`（male/female/neutral）、`age_style`（young/mature/senior）、`tone`、`pace`（slow/natural/fast）

后续配音和人物视频都以这一条为唯一角色来源。

## 三节

正好 3 个 Section，`role` 依次 `hook` / `body` / `close`。选一个叙事模式，例如 `Question→Evidence→Explanation→Implication`、`Myth→Evidence→Correction`、`Result→Reverse Engineering→Lesson`。

Hook 制造缺口，Body 给证据，Close 兑现 Hook。三节 `target_duration_ms` 总和接近 Brief 时长。

```json
{
  "sections": {
    "narrative_pattern": "Question→Evidence→Explanation→Implication",
    "one_sentence_thesis": "一句话主题",
    "world_state": {
      "topic_frame": "本片如何界定主题",
      "entities": [
        {
          "entity_id": "entity_topic",
          "name": "主题对象",
          "kind": "concept",
          "narrative_role": "topic",
          "description": "本片要解释的对象"
        }
      ],
      "claims": [
        {
          "claim_id": "wc01",
          "statement": "Brief 已给出的事实",
          "epistemic_status": "given_by_brief",
          "evidence_required": false
        }
      ],
      "causal_links": [
        {"cause": "尽早验证", "effect": "更早暴露风险", "explanation": "反馈还发生在上下文清楚的时候"}
      ],
      "open_questions": ["哪些风险最值得先处理？"],
      "narrative_boundaries": ["不声称能消除全部风险"],
      "aroll_character": null
    },
    "video_profile": {
      "requested": "auto",
      "resolved": "b_roll",
      "selection_source": "ai",
      "rationale": "适合画外音配说明画面",
      "speaker_presence_ratio_min": 0.0,
      "speaker_presence_ratio_max": 0.15,
      "character_consistency_required": false,
      "character_id": null,
      "character_description": null
    },
    "sections": [
      {"section_id": "section_hook", "role": "hook", "target_duration_ms": 15000, "goal": "制造缺口", "attention_strategy": "反常识问题"},
      {"section_id": "section_body", "role": "body", "target_duration_ms": 55000, "goal": "给证据", "attention_strategy": "证据与转折"},
      {"section_id": "section_close", "role": "close", "target_duration_ms": 20000, "goal": "兑现 Hook", "attention_strategy": "回扣开场"}
    ]
  }
}
```
