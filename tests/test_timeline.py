from pathlib import Path

from PIL import Image

from tests.test_editorial import FakeGenerator, _plan
from tests.test_voice import FakeTTS, _stage_one
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.stage_five.models import DerivedAsset
from ugc_harness.stage_five.pipeline import TimelineCompositionPipeline
from ugc_harness.stage_four.models import AssetCard
from ugc_harness.stage_four.pipeline import AssetAcquisitionPipeline
from ugc_harness.stage_four.providers import ProviderResult
from ugc_harness.stage_three.models import ExplorationDirection
from ugc_harness.stage_three.pipeline import EditorialStagePipeline
from ugc_harness.stage_two.pipeline import VoiceStagePipeline


class AllSuccessAssets:
    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat,
        direction: ExplorationDirection,
        project_dir: Path,
    ) -> ProviderResult:
        output = project_dir / "assets" / "test" / f"{visual_request_id}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), (30, 80, 140)).save(output)
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
            "素材成功",
        )


class FakeScreenAnimation:
    def generate(
        self,
        *,
        asset: AssetCard,
        beat,
        direction: ExplorationDirection,
        project_dir: Path,
    ) -> DerivedAsset:
        output = (
            project_dir
            / "assets"
            / "stage_five_generated_video"
            / "derived_vr01_screen_video.mp4"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(bytes(2048))
        return DerivedAsset(
            derivative_id="derived_vr01_screen_video",
            source_asset_id=asset.asset_id,
            derivation_type="ai_image_to_video",
            local_path=output.relative_to(project_dir).as_posix(),
            mime_type="video/mp4",
            sha256="1" * 64,
            generator_model="fake-video-model",
            generation_prompt="cursor, click and scroll",
            generation_job_id="job-test",
        )


def test_timeline_uses_audio_clock_and_ai_screen_derivative(
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
    plan.visual_requirements[0].directions = [
        ExplorationDirection(
            direction_id="vr01_d01",
            order=1,
            description="概念屏幕交互",
            visual_role="illustration",
            asset_type="screen_recording",
            grounding_requirement="contextual",
            generated_media_disclosure_required=False,
            must_not_imply=["不是实际产品录屏"],
        )
    ]
    stage_three = EditorialStagePipeline(
        FakeGenerator(plan),
        "fake-model",
    ).run(stage_one, stage_two)
    stage_four = AssetAcquisitionPipeline(AllSuccessAssets()).run(
        stage_three,
        stage_two.realized_beats,
        tmp_path,
    )

    artifact = TimelineCompositionPipeline(FakeScreenAnimation()).run(
        stage_two,
        stage_three,
        stage_four,
        tmp_path,
    )
    written = ArtifactWriter(tmp_path.parent).write_timeline_stage(
        tmp_path,
        artifact,
    )

    assert artifact.quality.passed is True
    assert artifact.quality.screen_derivative_count == 1
    assert artifact.timeline.duration_ms == stage_two.timed_audio.duration_ms
    assert artifact.timeline.clips[0].playback_modality == "video"
    assert artifact.timeline.clips[0].derivative_id is not None
    assert artifact.timeline.clips[-1].timeline_end_ms == (
        stage_two.timed_audio.duration_ms
    )
    assert artifact.captions
    assert any(path.name == "stage_five_artifact.json" for path in written)
