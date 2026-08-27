from pathlib import Path

from PIL import Image

from tests.test_assets import _asset_run
from tests.fixtures.tool_models import QueueToolModel
from tests.test_editorial import _editorial_run, _plan
from tests.test_timeline import AllSuccessAssets
from tests.test_voice import _narrative, _voice_run
from ugc_harness.agents.asset_agent.image_models import AssetInspection
from ugc_harness.agents.asset_agent.image_tools import ImagePreparationCapabilities
from ugc_harness.harness.asset_controller import AssetHarnessController


class FakeImageAnalyzer:
    def analyze(self, *, asset, beat, image_path: Path) -> AssetInspection:
        is_document = asset.modality in {
            "chart",
            "document_screenshot",
            "source_screenshot",
        }
        return AssetInspection(
            asset_id=asset.asset_id,
            analyzer_model="fake-vision-model",
            content_type="document" if is_document else "photo",
            focal_box=(0.2, 0.2, 0.6, 0.5),
            focus_confidence=0.9,
            preserve_full_frame=False,
            blocking_overlay=False,
            text_readability="good",
            key_text=["focus"],
            recommended_strategy="focus_crop" if is_document else "subject_cover",
            reason="test focal subject",
        )


def _inputs(tmp_path: Path):
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    factual_beat_id = next(
        beat.beat_id
        for beat in voice_run.artifact.realized_beats
        if beat.planned_beat_id == "pb04"
    )
    plan = _plan(
        [beat.beat_id for beat in voice_run.artifact.realized_beats],
        narrative.brief.project_id,
        factual_beat_id,
    )
    return voice_run, _editorial_run(narrative, voice_run, plan)


def test_asset_critic_creates_and_executes_image_repair_tasks(tmp_path: Path) -> None:
    voice_run, editorial_run = _inputs(tmp_path)
    run = _asset_run(editorial_run, voice_run, AllSuccessAssets(), tmp_path)

    assert run.artifact.quality.passed is True
    assert run.artifact.prepared_images
    for item in run.artifact.prepared_images:
        with Image.open(tmp_path / item.output_path) as image:
            assert image.size == (1080, 1920)
    history = run.record.project_state.trajectory.phases["asset"].tasks
    assert [item.task_kind for item in history[-2:]] == ["generation", "repair"]
    assert history[-2].evaluation.passed is False
    assert history[-1].evaluation.passed is True
    assert all(
        item.task.allowed_tools
        == ["asset.prepare_image", "asset.submit_candidate"]
        for item in history[-1:]
    )
    assert "prepared_image:asset_vr01" in run.record.project_state.dependency_graph.nodes
    graph = run.record.project_state.dependency_graph.nodes
    assert graph["asset_inspection:asset_vr01"].depends_on == [
        "asset:asset_vr01"
    ]
    assert set(graph["prepared_image:asset_vr01"].depends_on) == {
        "asset:asset_vr01",
        "asset_inspection:asset_vr01",
    }
    assert "prepared_image:asset_vr01" in graph[
        "asset_inspection:asset_vr01"
    ].dependents


def test_generated_image_uses_full_frame_subject_cover(tmp_path: Path) -> None:
    voice_run, editorial_run = _inputs(tmp_path)
    run = _asset_run(editorial_run, voice_run, AllSuccessAssets(), tmp_path)

    asset = run.artifact.assets[0].model_copy(update={"modality": "ai_image"})
    inspection = AssetInspection(
        asset_id=asset.asset_id,
        analyzer_model="fake-vision-model",
        content_type="illustration",
        focal_box=(0.0, 0.0, 1.0, 1.0),
        focus_confidence=0.9,
        preserve_full_frame=True,
        blocking_overlay=False,
        text_readability="none",
        recommended_strategy="contained_background",
        reason="preserve the generated frame",
    )
    prepared = ImagePreparationCapabilities().prepare_image(
        asset=asset,
        inspection=inspection,
        project_dir=tmp_path,
    )

    assert prepared.strategy == "subject_cover"


def test_login_overlay_is_rejected_without_crop_repair(tmp_path: Path) -> None:
    voice_run, editorial_run = _inputs(tmp_path)

    class BlockingAnalyzer(FakeImageAnalyzer):
        def analyze(self, *, asset, beat, image_path: Path) -> AssetInspection:
            return super().analyze(
                asset=asset, beat=beat, image_path=image_path
            ).model_copy(update={"blocking_overlay": True})

    run = AssetHarnessController.from_provider(
        AllSuccessAssets(),
        BlockingAnalyzer(),
        tool_model=QueueToolModel(),
    ).run(
        editorial_run.artifact,
        voice_run.artifact,
        tmp_path,
        editorial_run.record.project_state,
    )

    assert run.artifact.quality.passed is False
    assert run.artifact.quality.blocked_image_count > 0
    assert any(
        issue.code == "LOGIN_OR_BLOCKING_OVERLAY"
        and issue.repair_options == ["retry_next_direction"]
        for issue in run.record.evaluation.issues
    )
    assert not run.artifact.prepared_images
    assert len(run.record.project_state.trajectory.phases["asset"].tasks) == 1
