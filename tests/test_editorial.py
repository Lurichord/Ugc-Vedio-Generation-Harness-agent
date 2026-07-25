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
                    "evidence_required": True,
                    "interpretation_label_required": False,
                    "source_status": "research_required",
                    "if_unsupported": "modify_or_remove",
                }
            ],
            "evidence_requests": [
                {
                    "evidence_request_id": "er01",
                    "claim_id": "c01",
                    "claim_to_verify": "这是一条需要验证的事实",
                    "search_queries": ["测试事实 官方", "测试事实 报告"],
                    "acceptable_source_types": ["official_document"],
                    "preferred_publishers": [],
                    "verification_questions": ["原始文件是否直接支持？"],
                    "visual_evidence_desired": True,
                    "direct_visual_evidence_required": False,
                }
            ],
            "visual_requirements": [
                {
                    "visual_request_id": f"vr{index:02d}",
                    "beat_id": beat_id,
                    "primary_role": (
                        "evidence"
                        if beat_id == factual_beat_id
                        else "illustration"
                    ),
                    "supporting_roles": [],
                    "purpose": "承载当前信息",
                    "content_description": "展示当前信息的合适画面",
                    "preferred_modalities": (
                        ["document_screenshot"]
                        if beat_id == factual_beat_id
                        else ["real_video", "ai_video"]
                    ),
                    "search_queries": ["相关真实素材"],
                    "evidence_claim_ids": (
                        ["c01"] if beat_id == factual_beat_id else []
                    ),
                    "grounding_requirement": (
                        "source_exact"
                        if beat_id == factual_beat_id
                        else "contextual"
                    ),
                    "generated_media_allowed": beat_id != factual_beat_id,
                    "generated_media_can_satisfy_evidence": False,
                    "generated_media_disclosure_required": (
                        beat_id != factual_beat_id
                    ),
                    "must_not_imply": (
                        [] if beat_id == factual_beat_id else ["不是新闻现场"]
                    ),
                    "fallback_ladder": ["真实素材", "动态图形或口播字幕"],
                    "max_asset_count": 2,
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
    assert artifact.quality.factual_evidence_request_coverage == 1
    assert artifact.quality.planned_evidence_beat_coverage == 1
    assert (tmp_path / "12_claim_evidence_map.json").is_file()
    assert (tmp_path / "14_visual_requirements.json").is_file()
    assert any(path.name == "stage_three_artifact.json" for path in written)


def test_generated_media_can_never_satisfy_evidence() -> None:
    with pytest.raises(ValidationError):
        VisualRequirement.model_validate(
            {
                "visual_request_id": "vr01",
                "beat_id": "b01",
                "primary_role": "illustration",
                "supporting_roles": [],
                "purpose": "说明",
                "content_description": "AI 说明画面",
                "preferred_modalities": ["ai_image"],
                "search_queries": [],
                "evidence_claim_ids": [],
                "grounding_requirement": "contextual",
                "generated_media_allowed": True,
                "generated_media_can_satisfy_evidence": True,
                "generated_media_disclosure_required": True,
                "must_not_imply": ["不是事实证据"],
                "fallback_ladder": ["AI 图片", "动态字幕"],
                "max_asset_count": 1,
            }
        )


def test_evidence_visual_cannot_prefer_generated_media() -> None:
    with pytest.raises(ValidationError):
        VisualRequirement.model_validate(
            {
                "visual_request_id": "vr01",
                "beat_id": "b01",
                "primary_role": "evidence",
                "supporting_roles": ["illustration"],
                "purpose": "举证",
                "content_description": "报告截图与说明画面",
                "preferred_modalities": ["document_screenshot", "ai_image"],
                "search_queries": ["原始报告"],
                "evidence_claim_ids": ["c01"],
                "grounding_requirement": "source_exact",
                "generated_media_allowed": True,
                "generated_media_can_satisfy_evidence": False,
                "generated_media_disclosure_required": True,
                "must_not_imply": ["AI 图不是证据"],
                "fallback_ladder": ["来源截图", "AI 说明性图片"],
                "max_asset_count": 2,
            }
        )
