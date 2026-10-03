from pathlib import Path

from PIL import Image

from tests.fixtures.tool_models import CyclingToolModel
from tests.test_editorial import _editorial_run, _plan
from tests.test_assets import _asset_run
from tests.test_voice import _narrative, _voice_run


def _timeline_tool_model() -> CyclingToolModel:
    return CyclingToolModel(["timeline.compose", "timeline.submit_candidate"])
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.agents.timeline_agent.models import DerivedAsset
from ugc_harness.harness.timeline_controller import TimelineHarnessController
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


class VideoAssets:
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
        output = project_dir / "assets" / "test" / f"{visual_request_id}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(bytes(2048))
        return ProviderResult(
            AssetCard(
                asset_id=f"asset_{visual_request_id}",
                visual_request_id=visual_request_id,
                direction_id=direction.direction_id,
                beat_id=beat.beat_id,
                modality="ai_video",
                origin="generated",
                local_path=output.relative_to(project_dir).as_posix(),
                mime_type="video/mp4",
                sha256="0" * 64,
                duration_ms=2000,
                generated_media_disclosure_required=True,
                generator_model="fake-video-model",
                generation_prompt="test prompt",
            ),
            "success",
            "视频素材成功",
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
        ),
        ExplorationDirection(
            direction_id="vr01_d02",
            order=2,
            description="屏幕方向失败后的 AI 说明图",
            visual_role="illustration",
            asset_type="ai_image",
            grounding_requirement="contextual",
            generated_media_disclosure_required=True,
            must_not_imply=["不是实际产品录屏"],
        ),
    ]
    editorial_run = _editorial_run(narrative, voice_run, plan)
    editorial = editorial_run.artifact
    assets_run = _asset_run(
        editorial_run, voice_run, AllSuccessAssets(), tmp_path
    )

    run = TimelineHarnessController.from_provider(
        FakeScreenAnimation(), tool_model=_timeline_tool_model()
    ).run(
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
    assert all(
        item.overlay_type != "generated_media_disclosure"
        and item.text != "AI 生成画面"
        for item in artifact.overlays
    )
    assert any(path.name == "timeline_artifact.json" for path in written)
    assert run.record.transition.to_agent == "render_agent"
    assert run.record.project_state.evaluation.stages.render == "ready"
    assert not any(
        ref.startswith("clip:")
        for ref in run.record.project_state.dependency_graph.nodes
    )
    assert "artifact:timeline" not in run.record.project_state.dependency_graph.nodes
    assert run.record.project_state.trajectory.phases["timeline"].tasks


def test_timeline_scales_narration_to_video_duration(tmp_path: Path) -> None:
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
    editorial_run = _editorial_run(narrative, voice_run, plan)
    assets_run = _asset_run(
        editorial_run, voice_run, VideoAssets(), tmp_path
    )
    run = TimelineHarnessController.from_provider(
        FakeScreenAnimation(), tool_model=_timeline_tool_model()
    ).run(
        voice,
        editorial_run.artifact,
        assets_run.artifact,
        tmp_path,
        assets_run.record.project_state,
    )
    artifact = run.artifact
    expected = 2000 * len(voice.realized_beats)
    assert artifact.timeline.duration_ms == expected
    assert artifact.timeline.audio_file == "audio/narration_aligned.wav"
    assert (tmp_path / artifact.timeline.audio_file).is_file()
    assert all(
        clip.timeline_end_ms - clip.timeline_start_ms == 2000
        for clip in artifact.timeline.clips
    )
    assert all(
        clip.playback_policy == "fit_narration_to_video"
        for clip in artifact.timeline.clips
    )
    assert artifact.captions
    assert all(
        item.overlay_type != "generated_media_disclosure"
        for item in artifact.overlays
    )
    assert artifact.captions[-1].end_ms <= expected
    assert artifact.timeline.duration_ms != voice.timed_audio.duration_ms


def test_explainer_overlays_keep_source_without_ai_label() -> None:
    from ugc_harness.agents.asset_agent.models import AssetUsabilityReview, SourceTrace
    from ugc_harness.agents.timeline_agent.capabilities import _build_overlays
    from ugc_harness.agents.voice_agent.models import RealizedBeat

    beat = RealizedBeat(
        beat_id="beat_1",
        planned_beat_id="pb01",
        start_ms=0,
        end_ms=4000,
        duration_ms=4000,
        script_segment_ids=["seg_1"],
        audio_segment_ids=["audio_1"],
        narration="这是一个例子。",
        proposition="这是一个例子。",
        discourse_role="explanation",
        relation_to_previous="opening",
    )
    generated = AssetCard(
        asset_id="asset_ai",
        visual_request_id="visual_1",
        direction_id="dir_ai",
        beat_id=beat.beat_id,
        modality="ai_image",
        origin="generated",
        local_path="assets/ai.png",
        mime_type="image/png",
        sha256="0" * 64,
        generated_media_disclosure_required=True,
        generator_model="fake-image-model",
        generation_prompt="test prompt",
    )
    sourced = AssetCard(
        asset_id="asset_web",
        visual_request_id="visual_1",
        direction_id="dir_web",
        beat_id=beat.beat_id,
        modality="real_image",
        origin="downloaded",
        local_path="assets/web.png",
        mime_type="image/png",
        sha256="1" * 64,
        source=SourceTrace(
            source_url="https://example.com/article",
            publisher="示例出版社",
        ),
        usability_review=AssetUsabilityReview(
            reviewer_model="test",
            usable=True,
            login_or_auth_overlay=False,
            obstruction_level="none",
            reason="ok",
        ),
    )

    generated_overlays = _build_overlays(beat, generated, ["推断内容"])
    sourced_overlays = _build_overlays(beat, sourced, [])

    assert all(
        item.overlay_type != "generated_media_disclosure"
        and item.text != "AI 生成画面"
        for item in generated_overlays + sourced_overlays
    )
    assert [item.text for item in generated_overlays] == ["观点 / 推断"]
    assert [item.text for item in sourced_overlays] == ["来源：示例出版社"]
    assert sourced_overlays[0].overlay_type == "source_attribution"
    assert sourced_overlays[0].position == "bottom_left"
