import math
from pathlib import Path

from tests.fixtures.tool_models import CyclingToolModel
from tests.test_editorial import _editorial_run, _plan
from tests.test_timeline import (
    AllSuccessAssets,
    FakeScreenAnimation,
    _timeline_tool_model,
)
from tests.test_assets import _asset_run
from tests.test_voice import _narrative, _voice_run
from ugc_harness.harness.timeline_controller import TimelineHarnessController
from ugc_harness.agents.render_agent.capabilities import _build_composition
from ugc_harness.agents.render_agent.models import (
    RenderCandidate,
    RenderedMedia,
)
from ugc_harness.harness.render_controller import RenderHarnessController


def test_render_composition_uses_prepared_images_and_audio_clock(
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
    editorial_run = _editorial_run(narrative, voice_run, plan)
    editorial = editorial_run.artifact
    assets_run = _asset_run(
        editorial_run, voice_run, AllSuccessAssets(), tmp_path
    )
    timeline_run = TimelineHarnessController.from_provider(
        FakeScreenAnimation(), tool_model=_timeline_tool_model()
    ).run(
        voice,
        editorial,
        assets_run.artifact,
        tmp_path,
        assets_run.record.project_state,
    )
    composition, missing = _build_composition(
        tmp_path,
        voice,
        timeline_run.artifact,
    )

    assert missing == []
    assert composition.duration_ms == voice.timed_audio.duration_ms
    assert composition.duration_in_frames == math.ceil(
        voice.timed_audio.duration_ms * 30 / 1000
    )
    assert len(composition.clips) == len(voice.realized_beats)
    assert all(
        clip.media_path.startswith("assets/prepared_image/")
        for clip in composition.clips
        if clip.media_type == "image"
    )
    assert composition.audio_path == voice.timed_audio.audio_file

    def fake_render(*, voice, timeline_artifact, project_dir):
        composition, _ = _build_composition(project_dir, voice, timeline_artifact)
        outputs = []
        for kind, size in (("final", (1080, 1920)), ("preview", (540, 960))):
            path = project_dir / "video" / f"{kind}.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(2048))
            outputs.append(
                RenderedMedia(
                    kind=kind,
                    local_path=path.relative_to(project_dir).as_posix(),
                    sha256="1" * 64,
                    size_bytes=2048,
                    width=size[0],
                    height=size[1],
                    fps=30,
                    duration_ms=voice.timed_audio.duration_ms,
                    video_duration_ms=voice.timed_audio.duration_ms,
                    audio_duration_ms=voice.timed_audio.duration_ms,
                    container_duration_ms=voice.timed_audio.duration_ms,
                    video_codec="h264",
                    audio_codec="aac",
                    has_video=True,
                    has_audio=True,
                )
            )
        return RenderCandidate(
            project_id=voice.project_id,
            composition=composition,
            outputs=outputs,
        )

    render_run = RenderHarnessController.from_renderer(
        fake_render,
        tool_model=CyclingToolModel(
            ["render.execute", "render.submit_candidate"]
        ),
    ).run(
        voice,
        timeline_run.artifact,
        tmp_path,
        timeline_run.record.project_state,
    )
    assert render_run.artifact.quality.passed is True
    assert render_run.record.transition.to_agent == "project_complete"
    assert render_run.record.project_state.video.render_status == "passed"
    assert "artifact:render" in render_run.record.project_state.dependency_graph.nodes
    assert render_run.record.project_state.trajectory.phases["render"].tasks
