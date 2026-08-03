from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from tests.test_narrative_controller import FakeGenerator as NarrativeGenerator
from tests.test_voice import FakeTTS, _narrative, _voice_run
from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.agents.editorial_agent.models import EditorialPlan, VisualRequirement
from ugc_harness.harness.editorial_controller import EditorialHarnessController
from ugc_harness.harness.voice_controller import VoiceHarnessController
from ugc_harness.harness.dependencies import DependencyGraph

T = TypeVar("T", bound=BaseModel)


def _plan(
    beat_ids: list[str],
    project_id: str,
    factual_beat_id: str,
) -> EditorialPlan:
    return EditorialPlan.model_validate(
        {
            "project_id": project_id,
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
            "claims": [
                {
                    "claim_id": "c01",
                    "beat_id": factual_beat_id,
                    "script_segment_ids": ["ss04"],
                    "statement": "这是一条需要验证的事实",
                    "claim_type": "factual",
                    "importance": 0.9,
                    "interpretation_label_required": False,
                }
            ],
            "visual_requirements": [
                {
                    "visual_request_id": f"vr{index:02d}",
                    "beat_id": beat_id,
                    "purpose": "承载当前信息",
                    "selection_policy": "first_success",
                    "directions": (
                        [
                            {
                                "direction_id": f"vr{index:02d}_d01",
                                "order": 1,
                                "description": "原始文件截图",
                                "visual_role": "evidence",
                                "asset_type": "document_screenshot",
                                "query": "测试事实原始文件",
                                "covers_claim_ids": ["c01"],
                                "grounding_requirement": "source_exact",
                                "generated_media_disclosure_required": False,
                                "must_not_imply": [],
                            },
                            {
                                "direction_id": f"vr{index:02d}_d02",
                                "order": 2,
                                "description": "事实相关的说明性画面",
                                "visual_role": "illustration",
                                "asset_type": "ai_video",
                                "query": "事实相关说明画面",
                                "covers_claim_ids": ["c01"],
                                "grounding_requirement": "contextual",
                                "generated_media_disclosure_required": True,
                                "must_not_imply": ["不是事实证据"],
                            },
                        ]
                        if beat_id == factual_beat_id
                        else [
                            {
                                "direction_id": f"vr{index:02d}_d01",
                                "order": 1,
                                "description": "相关真实素材",
                                "visual_role": "context",
                                "asset_type": "real_image",
                                "query": "相关真实图片",
                                "covers_claim_ids": [],
                                "grounding_requirement": "contextual",
                                "generated_media_disclosure_required": False,
                                "must_not_imply": [],
                            },
                            {
                                "direction_id": f"vr{index:02d}_d02",
                                "order": 2,
                                "description": "说明性 AI 素材",
                                "visual_role": "illustration",
                                "asset_type": "ai_video",
                                "query": "说明性画面",
                                "covers_claim_ids": [],
                                "grounding_requirement": "contextual",
                                "generated_media_disclosure_required": True,
                                "must_not_imply": ["不是新闻现场"],
                            },
                        ]
                    ),
                }
                for index, beat_id in enumerate(beat_ids, start=1)
            ],
        }
    )


class FakeGenerator:
    settings = object()

    def __init__(self, plan: EditorialPlan):
        self.plan = plan

    def generate(self, prompt: str, output_type: type[T]) -> T:
        assert output_type is EditorialPlan
        return self.plan  # type: ignore[return-value]


class SequenceGenerator:
    def __init__(self, plans: list[EditorialPlan]):
        self.plans = plans
        self.prompts: list[str] = []

    def generate(self, prompt: str, output_type: type[T]) -> T:
        assert output_type is EditorialPlan
        self.prompts.append(prompt)
        return self.plans.pop(0)  # type: ignore[return-value]


def _editorial_run(narrative, voice_run, plan: EditorialPlan):
    return EditorialHarnessController.from_generator(
        FakeGenerator(plan), "fake-model"
    ).run(narrative, voice_run.artifact, voice_run.record.project_state)


def test_editorial_stage_covers_claims_and_realized_beats(
    tmp_path: Path,
) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        next(
            beat.beat_id
            for beat in voice.realized_beats
            if beat.planned_beat_id == "pb04"
        ),
    )

    run = _editorial_run(narrative, voice_run, plan)
    artifact = run.artifact
    written = ArtifactWriter(tmp_path.parent).write_editorial(
        tmp_path,
        artifact,
    )
    written.extend(
        ArtifactWriter(tmp_path.parent).write_editorial_run(tmp_path, run.record)
    )

    assert artifact.quality.passed is True
    assert artifact.quality.beat_visual_coverage == 1
    assert (tmp_path / "12_claim_map.json").is_file()
    assert (tmp_path / "13_visual_requirements.json").is_file()
    assert any(path.name == "editorial_artifact.json" for path in written)
    assert run.record.transition.to_agent == "asset_agent"
    assert run.record.project_state.video.asset_status == "ready"


def test_editorial_uses_third_candidate_after_revision_budget(
    tmp_path: Path,
) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        next(
            beat.beat_id
            for beat in voice.realized_beats
            if beat.planned_beat_id == "pb04"
        ),
    )
    plan.visual_requirements.pop()

    run = _editorial_run(narrative, voice_run, plan)

    assert run.record.evaluation.passed is True
    assert run.record.transition.to_agent == "asset_agent"
    assert "use_best_available" in run.record.transition.reason
    assert run.record.project_state.video.editorial_status == "passed"
    assert run.record.project_state.video.asset_status == "ready"
    tasks = run.record.project_state.trajectory.phases["editorial"].tasks
    assert len(tasks) == 3
    assert [item.evaluation.passed for item in tasks] == [False, False, True]
    assert tasks[-1].evaluation.issues[0].severity == "warning"


def test_complete_agent_chain_records_beat_graph_and_phase_tasks(
    tmp_path: Path,
) -> None:
    narrative_run = NarrativeHarnessController.from_generator(
        NarrativeGenerator(), "fake-model"
    ).run(make_brief(topic="依赖图集成测试", duration_seconds=90))
    narrative = narrative_run.artifact
    voice_run = VoiceHarnessController.from_provider(
        FakeTTS(), "test-voice"
    ).run(
        narrative,
        tmp_path,
        narrative_run.record.project_state,
    )
    voice = voice_run.artifact
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        next(
            beat.beat_id
            for beat in voice.realized_beats
            if beat.planned_beat_id == "pb04"
        ),
    )

    editorial_run = _editorial_run(narrative, voice_run, plan)
    state = editorial_run.record.project_state
    nodes = state.dependency_graph.nodes
    DependencyGraph(state.dependency_graph).validate_integrity()

    assert set(state.trajectory.phases) == {"narrative", "voice", "editorial"}
    assert all(
        phase.tasks[0].task_kind == "generation"
        for phase in state.trajectory.phases.values()
    )
    assert all(
        phase.tasks[0].graph_update.committed
        for phase in state.trajectory.phases.values()
    )
    first_beat = voice.realized_beats[0]
    first_visual = plan.visual_requirements[0]
    beat_ref = f"realized_beat:{first_beat.beat_id}"
    visual_ref = f"visual_requirement:{first_visual.visual_request_id}"
    assert beat_ref in nodes
    assert visual_ref in nodes
    assert beat_ref in nodes[visual_ref].depends_on
    assert visual_ref in nodes[beat_ref].dependents
    assert any(
        snapshot.ref == beat_ref
        for snapshot in editorial_run.record.task.dependency_snapshot
    )


def test_editorial_revision_appends_task_to_same_phase(
    tmp_path: Path,
) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    good_plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        next(
            beat.beat_id
            for beat in voice.realized_beats
            if beat.planned_beat_id == "pb04"
        ),
    )
    rejected_plan = good_plan.model_copy(deep=True)
    rejected_plan.visual_requirements.pop()
    generator = SequenceGenerator([rejected_plan, good_plan])
    revised = EditorialHarnessController.from_generator(
        generator, "fake-model"
    ).run(
        narrative,
        voice,
        voice_run.record.project_state,
    )

    tasks = revised.record.project_state.trajectory.phases["editorial"].tasks
    assert len(tasks) == 2
    assert [task.task_kind for task in tasks] == ["generation", "revision"]
    assert tasks[0].graph_update.committed is False
    assert tasks[1].graph_update.committed is True
    assert tasks[0].task.task_id != tasks[1].task.task_id
    assert revised.record.project_state.video.editorial_status == "passed"
    assert revised.record.project_state.video.asset_status == "ready"
    assert "上一版" in generator.prompts[1]
    assert "VisualRequirement" in generator.prompts[1]


def test_generated_media_can_never_be_an_evidence_direction() -> None:
    with pytest.raises(ValidationError):
        VisualRequirement.model_validate(
            {
                "visual_request_id": "vr01",
                "beat_id": "b01",
                "purpose": "举证",
                "selection_policy": "first_success",
                "directions": [
                    {
                        "direction_id": "vr01_d01",
                        "order": 1,
                        "description": "AI 证据图",
                        "visual_role": "evidence",
                        "asset_type": "ai_image",
                        "query": "AI 证据图",
                        "covers_claim_ids": ["c01"],
                        "grounding_requirement": "source_exact",
                        "generated_media_disclosure_required": True,
                        "must_not_imply": ["不是事实证据"],
                    }
                ],
            }
        )


def test_first_success_directions_must_be_contiguous() -> None:
    with pytest.raises(ValidationError):
        VisualRequirement.model_validate(
            {
                "visual_request_id": "vr01",
                "beat_id": "b01",
                "purpose": "说明",
                "selection_policy": "first_success",
                "directions": [
                    {
                        "direction_id": "vr01_d02",
                        "order": 2,
                        "description": "错误地从第二方向开始",
                        "visual_role": "context",
                        "asset_type": "real_image",
                        "query": "相关素材",
                        "covers_claim_ids": [],
                        "grounding_requirement": "contextual",
                        "generated_media_disclosure_required": False,
                        "must_not_imply": [],
                    }
                ],
            }
        )


def test_web_video_is_not_an_mvp_asset_type() -> None:
    with pytest.raises(ValidationError):
        VisualRequirement.model_validate(
            {
                "visual_request_id": "vr01",
                "beat_id": "b01",
                "purpose": "提供现实场景",
                "selection_policy": "first_success",
                "directions": [
                    {
                        "direction_id": "vr01_d01",
                        "order": 1,
                        "description": "联网搜索真实视频",
                        "visual_role": "context",
                        "asset_type": "real_video",
                        "query": "数据中心视频",
                        "covers_claim_ids": [],
                        "grounding_requirement": "contextual",
                        "generated_media_disclosure_required": False,
                        "must_not_imply": [],
                    }
                ],
            }
        )
