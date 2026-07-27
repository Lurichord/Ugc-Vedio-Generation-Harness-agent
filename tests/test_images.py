from pathlib import Path

from PIL import Image

from tests.test_editorial import FakeGenerator, _plan
from tests.test_timeline import AllSuccessAssets, FakeScreenAnimation
from tests.test_voice import FakeTTS, _stage_one
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.stage_five.pipeline import TimelineCompositionPipeline
from ugc_harness.stage_four.pipeline import AssetAcquisitionPipeline
from ugc_harness.stage_seven.models import ImageAnalysis
from ugc_harness.stage_seven.pipeline import ImagePreparationPipeline
from ugc_harness.stage_three.pipeline import EditorialStagePipeline
from ugc_harness.stage_two.pipeline import VoiceStagePipeline


class FakeImageAnalyzer:
    def analyze(self, *, asset, beat, image_path: Path) -> ImageAnalysis:
        is_document = asset.modality in {
            "chart",
            "document_screenshot",
            "source_screenshot",
        }
        return ImageAnalysis(
            asset_id=asset.asset_id,
            analyzer_model="fake-vision-model",
            content_type="document" if is_document else "photo",
            focal_box=(0.2, 0.2, 0.6, 0.5),
            focus_confidence=0.9,
            preserve_full_frame=False,
            blocking_overlay=False,
            text_readability="good",
            key_text=["重点"],
            recommended_strategy=(
                "focus_crop" if is_document else "subject_cover"
            ),
            reason="测试主体",
        )


def test_image_preparation_outputs_render_ready_portraits(
    tmp_path: Path,
) -> None:
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
    stage_three = EditorialStagePipeline(
        FakeGenerator(plan),
        "fake-model",
    ).run(stage_one, stage_two)
    stage_four = AssetAcquisitionPipeline(AllSuccessAssets()).run(
        stage_three,
        stage_two.realized_beats,
        tmp_path,
    )
    stage_five = TimelineCompositionPipeline(FakeScreenAnimation()).run(
        stage_two,
        stage_three,
        stage_four,
        tmp_path,
    )
    artifact = ImagePreparationPipeline(FakeImageAnalyzer()).run(
        stage_two,
        stage_four,
        stage_five,
        tmp_path,
    )
    written = ArtifactWriter(
        tmp_path.parent
    ).write_image_preparation_stage(tmp_path, artifact)

    assert artifact.quality.passed is True
    assert artifact.quality.processed_image_count == len(
        stage_five.timeline.clips
    )
    for item in artifact.processed_images:
        with Image.open(tmp_path / item.output_path) as image:
            assert image.size == (1080, 1920)
        source_asset = next(
            asset
            for asset in stage_four.assets
            if asset.asset_id == item.asset_id
        )
        if (
            source_asset.origin == "generated"
            and source_asset.modality == "ai_image"
        ):
            assert item.strategy == "subject_cover"
    assert any(path.name == "stage_seven_artifact.json" for path in written)


def test_generated_image_never_uses_stacked_contained_layout(
    tmp_path: Path,
) -> None:
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
    stage_three = EditorialStagePipeline(
        FakeGenerator(plan),
        "fake-model",
    ).run(stage_one, stage_two)
    stage_four = AssetAcquisitionPipeline(AllSuccessAssets()).run(
        stage_three,
        stage_two.realized_beats,
        tmp_path,
    )
    stage_five = TimelineCompositionPipeline(FakeScreenAnimation()).run(
        stage_two,
        stage_three,
        stage_four,
        tmp_path,
    )
    image_asset_id = next(
        clip.source_asset_id
        for clip in stage_five.timeline.clips
        if clip.playback_modality == "image"
    )
    stage_four.assets = [
        asset.model_copy(update={"modality": "ai_image"})
        if asset.asset_id == image_asset_id
        else asset
        for asset in stage_four.assets
    ]

    class PreserveFullFrameAnalyzer(FakeImageAnalyzer):
        def analyze(self, *, asset, beat, image_path: Path) -> ImageAnalysis:
            analysis = super().analyze(
                asset=asset,
                beat=beat,
                image_path=image_path,
            )
            return analysis.model_copy(
                update={
                    "preserve_full_frame": True,
                    "recommended_strategy": "contained_background",
                }
            )

    artifact = ImagePreparationPipeline(PreserveFullFrameAnalyzer()).run(
        stage_two,
        stage_four,
        stage_five,
        tmp_path,
    )

    generated_images = [
        item
        for item in artifact.processed_images
        if item.asset_id == image_asset_id
    ]
    assert generated_images
    assert all(item.strategy == "subject_cover" for item in generated_images)
