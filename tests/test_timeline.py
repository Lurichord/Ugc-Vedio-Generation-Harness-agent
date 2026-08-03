from pathlib import Path

from PIL import Image

from tests.test_editorial import _editorial_run, _plan
from tests.test_assets import _asset_run
from tests.test_voice import _narrative, _voice_run
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.agents.timeline_agent.models import DerivedAsset
from ugc_harness.harness.timeline_controller import TimelineHarnessController
from ugc_harness.harness.repair import RepairScheduler
from ugc_harness.agents.asset_agent.models import AssetCard
from ugc_harness.agents.asset_agent.providers import ProviderResult
from ugc_harness.agents.editorial_agent.models import ExplorationDirection


class AllSuccessAssets:
    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat,
        direction: ExplorationDirection,
        project_dir: Path,
        **kwargs,
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
            / "timeline_generated_video"
            / "derived_vr01_screen_video.mp4"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(bytes(2048))
        return DerivedAsset(
            derivative_id="derived_vr01_screen_video",
            source_asset_id=asset.asset_id,
            beat_id=beat.beat_id,
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
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    factual_beat_id = next(
        beat.beat_id
        for beat in voice.realized_beats
        if beat.planned_beat_id == "pb04"
    )
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
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
    editorial_run = _editorial_run(narrative, voice_run, plan)
    editorial = editorial_run.artifact
    assets_run = _asset_run(
        editorial_run, voice_run, AllSuccessAssets(), tmp_path
    )

    run = TimelineHarnessController.from_provider(FakeScreenAnimation()).run(
        voice,
        editorial,
        assets_run.artifact,
        tmp_path,
        assets_run.record.project_state,
    )
    artifact = run.artifact
    writer = ArtifactWriter(tmp_path.parent)
    written = writer.write_timeline(tmp_path, artifact)
    written.extend(writer.write_timeline_run(tmp_path, run.record))

    assert artifact.quality.passed is True
    assert artifact.quality.screen_derivative_count == 1
    assert artifact.timeline.duration_ms == voice.timed_audio.duration_ms
    assert artifact.timeline.clips[0].playback_modality == "video"
    assert artifact.timeline.clips[0].derivative_id is not None
    assert artifact.timeline.clips[-1].timeline_end_ms == (
        voice.timed_audio.duration_ms
    )
    assert artifact.captions
    assert any(path.name == "timeline_artifact.json" for path in written)
    assert run.record.transition.to_agent == "render_agent"
    assert run.record.project_state.video.render_status == "ready"
    assert "artifact:timeline" in run.record.project_state.dependency_graph.nodes
    assert run.record.project_state.trajectory.phases["timeline"].tasks

    state = run.record.project_state
    first_beat_id = voice.realized_beats[0].beat_id
    for ref in {
        f"timeline_clip:{first_beat_id}",
        f"timeline_transform:{first_beat_id}",
        "artifact:timeline",
    }:
        state.dependency_graph.nodes[ref].status = "stale"
    repair_task = RepairScheduler().plan(
        state, ["artifact:timeline"]
    ).tasks[0]
    repaired = TimelineHarnessController.from_provider(
        FakeScreenAnimation()
    ).run(
        voice,
        editorial,
        assets_run.artifact,
        tmp_path,
        state,
        repair_task,
        current_artifact=artifact,
    )

    realized_ids = {item.beat_id for item in voice.realized_beats}
    assert set(repair_task.scope.beat_ids) & realized_ids == {first_beat_id}
    assert repaired.record.transition.to_agent == "repair_scheduler"
    assert repaired.record.project_state.trajectory.phases[
        "timeline"
    ].tasks[-1].task_kind == "repair"
