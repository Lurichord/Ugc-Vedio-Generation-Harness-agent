from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from tests.test_voice import FakeTTS, _stage_one
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.stage_three.models import EditorialPlan, VisualRequirement
from ugc_harness.stage_three.pipeline import EditorialStagePipeline
from ugc_harness.stage_two.pipeline import VoiceStagePipeline

T = TypeVar("T", bound=BaseModel)


def _plan(
    beat_ids: list[str],
    project_id: str,
    factual_beat_id: str,
) -> EditorialPlan:
    return EditorialPlan.model_validate(
        {
            "project_id": project_id,
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


def test_editorial_stage_covers_claims_and_realized_beats(
    tmp_path: Path,
) -> None:
    stage_one = _stage_one()
    stage_two = VoiceStagePipeline(FakeTTS(), "test-voice").run(
        stage_one,
        tmp_path,
    )
    plan = _plan(
        [beat.beat_id for beat in stage_two.realized_beats],
        stage_one.brief.project_id,
        next(
            beat.beat_id
            for beat in stage_two.realized_beats
            if beat.planned_beat_id == "pb04"
        ),
    )

    artifact = EditorialStagePipeline(
        FakeGenerator(plan), "fake-model"
    ).run(stage_one, stage_two)
    written = ArtifactWriter(tmp_path.parent).write_editorial_stage(
        tmp_path,
        artifact,
    )

    assert artifact.quality.passed is True
    assert artifact.quality.beat_visual_coverage == 1
    assert (tmp_path / "12_claim_map.json").is_file()
    assert (tmp_path / "13_visual_requirements.json").is_file()
    assert any(path.name == "stage_three_artifact.json" for path in written)


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
