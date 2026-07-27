from pathlib import Path

from tests.test_editorial import FakeGenerator, _plan
from tests.test_voice import FakeTTS, _stage_one
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.shared.settings import LLMSettings
from ugc_harness.stage_four.models import AssetCard
from ugc_harness.stage_four.models import AssetUsabilityReview
from ugc_harness.stage_four.pipeline import AssetAcquisitionPipeline
from ugc_harness.stage_four.providers import OpenRouterWebAssetProvider
from ugc_harness.stage_four.providers import ProviderResult
from ugc_harness.stage_four.providers import _parse_asset_review
from ugc_harness.stage_four.providers import _validate_public_url
from ugc_harness.stage_three.models import ExplorationDirection
from ugc_harness.stage_three.pipeline import EditorialStagePipeline
from ugc_harness.stage_two.pipeline import VoiceStagePipeline


class FakeAssetProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat,
        direction: ExplorationDirection,
        project_dir: Path,
    ) -> ProviderResult:
        self.calls.append(direction.direction_id)
        if direction.order == 1:
            return ProviderResult(None, "not_found", "第一个方向没有素材")
        output = project_dir / "assets" / "test" / f"{visual_request_id}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(1024))
        return ProviderResult(
            AssetCard(
                asset_id=f"asset_{visual_request_id}",
                visual_request_id=visual_request_id,
                direction_id=direction.direction_id,
                beat_id=beat.beat_id,
                modality=direction.asset_type,
                origin="generated",
                local_path=output.relative_to(project_dir).as_posix(),
                mime_type="image/png",
                sha256="0" * 64,
                generated_media_disclosure_required=True,
                generator_model="fake-image-model",
                generation_prompt="test prompt",
            ),
            "success",
            "第二个方向成功",
        )


def test_asset_pipeline_stops_after_first_success(tmp_path: Path) -> None:
    stage_one = _stage_one()
    stage_two = VoiceStagePipeline(FakeTTS(), "test-voice").run(
        stage_one,
        tmp_path,
    )
    factual_beat_id = next(
        beat.beat_id
        for beat in stage_two.realized_beats
        if beat.planned_beat_id == "pb04"
    )
    plan = _plan(
        [beat.beat_id for beat in stage_two.realized_beats],
        stage_one.brief.project_id,
        factual_beat_id,
    )
    first_visual = plan.visual_requirements[0]
    first_visual.directions.append(
        ExplorationDirection(
            direction_id="vr01_d03",
            order=3,
            description="不应该被调用的方向",
            visual_role="illustration",
            asset_type="ai_image",
            query="不应调用",
            grounding_requirement="contextual",
            generated_media_disclosure_required=True,
            must_not_imply=["不是事实记录"],
        )
    )
    stage_three = EditorialStagePipeline(
        FakeGenerator(plan), "fake-model"
    ).run(stage_one, stage_two)
    provider = FakeAssetProvider()

    artifact = AssetAcquisitionPipeline(provider).run(
        stage_three,
        stage_two.realized_beats,
        tmp_path,
    )
    written = ArtifactWriter(tmp_path.parent).write_asset_stage(
        tmp_path,
        artifact,
    )

    assert artifact.quality.passed is True
    assert artifact.quality.resolution_coverage == 1
    assert "vr01_d03" not in provider.calls
    assert all(
        len(resolution.attempts) == 2
        for resolution in artifact.resolutions
    )
    assert (tmp_path / "15_asset_cards.json").is_file()
    assert any(path.name == "stage_four_artifact.json" for path in written)


def test_asset_url_guard_rejects_private_ip_but_allows_public_domain() -> None:
    _validate_public_url("https://example.com/report")

    import pytest

    with pytest.raises(ValueError):
        _validate_public_url("http://127.0.0.1/private")


def test_asset_review_parser_records_login_obstruction() -> None:
    review = _parse_asset_review(
        """{"usable":false,"login_or_auth_overlay":true,
        "obstruction_level":"major","reason":"登录弹窗覆盖主体"}""",
        "review-model",
    )

    assert review.usable is False
    assert review.login_or_auth_overlay is True
    assert review.obstruction_level == "major"


def test_web_provider_deletes_login_blocked_capture(tmp_path: Path) -> None:
    class LoginBlockedProvider(OpenRouterWebAssetProvider):
        def _search_one_source(self, direction, beat):
            return {
                "url": "https://example.com/report",
                "title": "Report",
                "publisher": "Example",
            }

        def _capture_page(self, url: str, output: Path) -> None:
            output.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(1024))

        def _review_web_asset(self, image_path: Path, mime_type: str):
            return AssetUsabilityReview(
                reviewer_model="fake-reviewer",
                usable=False,
                login_or_auth_overlay=True,
                obstruction_level="major",
                reason="登录弹窗覆盖主体",
            )

    stage_two = VoiceStagePipeline(FakeTTS(), "test-voice").run(
        _stage_one(),
        tmp_path,
    )
    direction = ExplorationDirection(
        direction_id="vr_login_d01",
        order=1,
        description="网页报告截图",
        visual_role="context",
        asset_type="source_screenshot",
        query="report",
        grounding_requirement="contextual",
    )
    provider = LoginBlockedProvider(
        LLMSettings(
            api_key="test-api-key",
            base_url="https://openrouter.ai/api/v1",
        ),
        edge_path=tmp_path / "fake-edge.exe",
    )
    try:
        result = provider.acquire(
            project_id="test",
            visual_request_id="vr_login",
            beat=stage_two.realized_beats[0],
            direction=direction,
            project_dir=tmp_path,
        )
    finally:
        provider.close()

    assert result.asset is None
    assert result.status == "not_found"
    assert "登录" in result.reason
    assert not (
        tmp_path / "assets" / "source_screenshot" / "asset_vr_login.png"
    ).exists()
