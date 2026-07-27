import math
from pathlib import Path

from tests.test_editorial import FakeGenerator, _plan
from tests.test_images import FakeImageAnalyzer
from tests.test_timeline import AllSuccessAssets, FakeScreenAnimation
from tests.test_voice import FakeTTS, _stage_one
from ugc_harness.stage_five.pipeline import TimelineCompositionPipeline
from ugc_harness.stage_four.pipeline import AssetAcquisitionPipeline
from ugc_harness.stage_seven.pipeline import ImagePreparationPipeline
from ugc_harness.stage_six.pipeline import _build_composition
from ugc_harness.stage_three.pipeline import EditorialStagePipeline
from ugc_harness.stage_two.pipeline import VoiceStagePipeline


def test_render_composition_uses_processed_images_and_audio_clock(
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
    stage_seven = ImagePreparationPipeline(FakeImageAnalyzer()).run(
        stage_two,
        stage_four,
        stage_five,
        tmp_path,
    )

    composition, missing = _build_composition(
        tmp_path,
        stage_two,
        stage_five,
        stage_seven,
    )

    assert missing == []
    assert composition.duration_ms == stage_two.timed_audio.duration_ms
    assert composition.duration_in_frames == math.ceil(
        stage_two.timed_audio.duration_ms * 30 / 1000
    )
    assert len(composition.clips) == len(stage_two.realized_beats)
    assert all(
        clip.media_path.startswith("assets/processed_image/")
        for clip in composition.clips
    )
    assert composition.audio_path == stage_two.timed_audio.audio_file
