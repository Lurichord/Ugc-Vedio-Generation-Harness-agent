from ugc_harness.agents.narrative_agent import (
    PlanningArtifact,
    ScriptArtifact,
    make_brief,
)
from ugc_harness.agents.narrative_agent.quality import evaluate


def sample_plan() -> PlanningArtifact:
    roles = [
        ("pb01", "section_hook", "question", "opening"),
        ("pb02", "section_hook", "reveal", "contrast"),
        ("pb03", "section_body", "claim", "continuation"),
        ("pb04", "section_body", "evidence", "evidence_for"),
        ("pb05", "section_body", "explanation", "cause"),
        ("pb06", "section_body", "implication", "escalation"),
        ("pb07", "section_close", "payoff", "resolution"),
        ("pb08", "section_close", "callback", "callback"),
    ]
    return PlanningArtifact.model_validate(
        {
            "narrative_pattern": "Question→Evidence→Explanation→Implication",
            "one_sentence_thesis": "核心判断",
            "world_state": {
                "topic_frame": "解释测试为什么重要",
                "entities": [
                    {
                        "entity_id": "entity_testing",
                        "name": "软件测试",
                        "kind": "concept",
                        "narrative_role": "核心解释对象",
                        "description": "用于发现风险并验证行为的工程活动",
                    }
                ],
                "claims": [
                    {
                        "claim_id": "wc01",
                        "statement": "测试可以暴露软件风险",
                        "epistemic_status": "to_verify",
                        "evidence_required": True,
                    }
                ],
                "causal_links": [
                    {
                        "cause": "尽早执行测试",
                        "effect": "更早暴露风险",
                        "explanation": "反馈发生在变更上下文仍然清晰的时候",
                    }
                ],
                "open_questions": ["哪些风险最值得优先测试？"],
                "narrative_boundaries": ["不声称测试能够消除所有缺陷"],
            },
            "video_profile": {
                "requested": "auto",
                "resolved": "b_roll",
                "selection_source": "ai",
                "rationale": "测试主题适合画外音配说明画面",
                "speaker_presence_ratio_min": 0.0,
                "speaker_presence_ratio_max": 0.15,
                "character_consistency_required": False,
                "character_id": None,
                "character_description": None,
            },
            "sections": [
                {
                    "section_id": "section_hook",
                    "role": "hook",
                    "target_duration_ms": 15000,
                    "goal": "建立信息缺口",
                    "attention_strategy": "反常识问题",
                },
                {
                    "section_id": "section_body",
                    "role": "body",
                    "target_duration_ms": 55000,
                    "goal": "解释",
                    "attention_strategy": "证据与转折",
                },
                {
                    "section_id": "section_close",
                    "role": "close",
                    "target_duration_ms": 20000,
                    "goal": "回扣",
                    "attention_strategy": "明确结论",
                },
            ],
            "beats": [
                {
                    "planned_beat_id": beat_id,
                    "section_id": section_id,
                    "order": i,
                    "semantic_goal": f"认知推进 {i}",
                    "discourse_role": role,
                    "relation_to_previous": relation,
                    "target_effect": "理解",
                    "target_duration_ms": 11250,
                    "audience_delta": {
                        "knowledge_added": [f"信息 {i}"],
                        "belief_update": None,
                        "question_added": "为什么？" if i == 1 else None,
                        "question_resolved": "为什么？" if i == 7 else None,
                        "emotion_target": "curiosity",
                    },
                    "evidence_need": {
                        "required": role == "evidence",
                        "claim_to_verify": "待核实事实" if role == "evidence" else None,
                        "acceptable_source_types": (
                            ["official_report"] if role == "evidence" else []
                        ),
                    },
                    "visual_intent_hint": "表达当前信息功能",
                }
                for i, (beat_id, section_id, role, relation) in enumerate(
                    roles, start=1
                )
            ],
        }
    )


def sample_script(plan: PlanningArtifact) -> ScriptArtifact:
    filler = "这是一段自然口语表达，用来清楚推进当前信息，让观众理解这一步为什么重要。"
    return ScriptArtifact.model_validate(
        {
            "script_version": "v1",
            "title_options": ["标题一", "标题二", "标题三"],
            "segments": [
                {
                    "script_segment_id": f"ss{i:02d}",
                    "planned_beat_id": beat.planned_beat_id,
                    "text": filler * 2,
                    "delivery_hint": {
                        "speech_act": beat.discourse_role,
                        "emphasis_words": ["重要"],
                        "pause_before_ms": 0,
                        "pause_after_ms": 100,
                        "energy": "medium",
                    },
                }
                for i, beat in enumerate(plan.beats, start=1)
            ],
        }
    )


def test_quality_passes_complete_beat_aware_script() -> None:
    brief = make_brief(topic="测试主题", duration_seconds=90)
    plan = sample_plan()
    report = evaluate(brief, plan, sample_script(plan))

    assert report.passed is True
    assert report.beat_coverage == 1.0
    assert report.evidence_claim_count == 1


def test_quality_rejects_missing_beat() -> None:
    brief = make_brief(topic="测试主题", duration_seconds=90)
    plan = sample_plan()
    script = sample_script(plan)
    script.segments.pop()

    report = evaluate(brief, plan, script)

    assert report.passed is False
    assert any(issue.code == "MISSING_BEAT_SCRIPT" for issue in report.issues)
