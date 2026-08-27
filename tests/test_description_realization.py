from types import SimpleNamespace

from ugc_harness.harness.description_builder import (
    build_video_description,
    initial_execution_state,
)
from ugc_harness.harness.description_realization import (
    apply_render_realization,
    apply_shot_media,
    apply_shot_timeline_realization,
    apply_voice_realization,
)
from ugc_harness.harness.models import ProjectState, RuntimeContext, VideoState
from ugc_harness.harness.shot_asset_controller import (
    ShotAssetArtifact,
    ShotAssetQuality,
    ShotVideoAsset,
)
from ugc_harness.harness.shot_timeline_controller import (
    ShotTimelineArtifact,
    ShotTimelineClip,
)

from tests.test_description import _drama_artifact
from tests.test_narrative_controller import (
    FakeGenerator,
    narrative_controller_from_generator,
)


def _drama_state() -> ProjectState:
    artifact = _drama_artifact()
    description = build_video_description(artifact)
    return ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(project_id=artifact.brief.project_id, state_version=1),
        description=description,
        execution=initial_execution_state(description),
    )


def _shot_assets(state: ProjectState) -> ShotAssetArtifact:
    assert state.description is not None
    assets = [
        ShotVideoAsset(
            asset_id=f"asset_{shot.spec.shot_id}",
            shot_id=shot.spec.shot_id,
            local_path=f"assets/generated_shots/asset_{shot.spec.shot_id}.mp4",
            mime_type="video/mp4",
            sha256="f" * 64,
            generator_model="seedance-test",
            generation_prompt="prompt",
            generation_job_id=f"job_{shot.spec.shot_id}",
            duration_ms=5_000,
            generation_cost_usd=0.2,
            audio_mode="embedded_in_video",
            preserve_source_audio=True,
        )
        for shot in state.description.shots
    ]
    return ShotAssetArtifact(
        project_id=state.video.project_id,
        format_id="drama",
        assets=assets,
        quality=ShotAssetQuality(
            passed=True,
            shot_count=len(assets),
            ai_video_count=len(assets),
        ),
    )


def test_shot_media_realization_updates_document_and_tags() -> None:
    state = _drama_state()
    apply_shot_media(state, _shot_assets(state))

    assert state.description is not None
    for shot in state.description.shots:
        assert shot.media is not None
        assert shot.media.modality == "ai_video"
        assert shot.media.generation_job_id == f"job_{shot.spec.shot_id}"
        tag = state.execution.elements[f"shot:{shot.spec.shot_id}"]
        assert tag.attempts == 1
        assert tag.generation_job_id == f"job_{shot.spec.shot_id}"
        assert tag.cost_usd == 0.2


def test_shot_timeline_realization_adds_clips_and_refs() -> None:
    state = _drama_state()
    assets = _shot_assets(state)
    apply_shot_media(state, assets)

    cursor = 0
    clips = []
    for asset in assets.assets:
        clips.append(
            ShotTimelineClip(
                clip_id=f"clip_{asset.shot_id}",
                shot_id=asset.shot_id,
                start_ms=cursor,
                end_ms=cursor + asset.duration_ms,
                playback_path=asset.local_path,
                audio_mode=asset.audio_mode,
                preserve_source_audio=True,
            )
        )
        cursor += asset.duration_ms
    timeline = ShotTimelineArtifact(
        project_id=state.video.project_id,
        format_id="drama",
        duration_ms=cursor,
        clips=clips,
    )
    apply_shot_timeline_realization(state, timeline)

    assert state.description is not None
    document_timeline = state.description.timeline
    assert document_timeline is not None
    assert document_timeline.duration_ms == cursor
    assert document_timeline.audio_file is None
    assert [clip.shot_ref for clip in document_timeline.clips] == [
        f"shot:{asset.shot_id}" for asset in assets.assets
    ]
    for clip in document_timeline.clips:
        assert f"clip:{clip.clip_id}" in state.execution.elements


def test_render_realization_fills_deliverable() -> None:
    state = _drama_state()
    render = SimpleNamespace(
        outputs=[
            SimpleNamespace(
                kind="final",
                local_path="video/final.mp4",
                sha256="a" * 64,
                duration_ms=30_000,
                width=1080,
                height=1920,
                fps=30.0,
            )
        ],
        composition=SimpleNamespace(renderer="remotion", renderer_version="4.0.499"),
        generated_at="2026-08-20T00:00:00+00:00",
    )
    apply_render_realization(state, render)  # type: ignore[arg-type]

    assert state.description is not None
    deliverable = state.description.deliverable
    assert deliverable is not None
    assert deliverable.video_file == "video/final.mp4"
    assert deliverable.width == 1080 and deliverable.height == 1920
    assert deliverable.renderer == "remotion@4.0.499"
    assert deliverable.rendered_at == "2026-08-20T00:00:00+00:00"


def test_voice_realization_binds_utterances() -> None:
    brief_run = narrative_controller_from_generator(
        FakeGenerator(), "fake-model"
    ).run(
        __import__(
            "ugc_harness.agents.narrative_agent", fromlist=["make_brief"]
        ).make_brief(topic="配音回填测试", duration_seconds=90)
    )
    state = brief_run.record.project_state
    assert state.description is not None and state.description.voice is not None
    utterances = state.description.voice.utterances

    segments = [
        SimpleNamespace(
            script_segment_id=utterance.utterance_id,
            file="voice/segments/seg.wav",
            start_ms=index * 1_000,
            end_ms=index * 1_000 + 900,
            duration_ms=900,
        )
        for index, utterance in enumerate(utterances)
    ]
    voice = SimpleNamespace(
        voice_plan=SimpleNamespace(
            speaker=SimpleNamespace(
                provider="volcengine",
                voice_id="voice_1",
                language="zh-CN",
                persona="知识型创作者",
                character_id=None,
            )
        ),
        timed_audio=SimpleNamespace(
            audio_file="voice/narration.wav",
            duration_ms=60_000,
            sample_rate=24_000,
            channels=1,
            segments=segments,
        ),
        word_alignment=SimpleNamespace(coverage=0.98, word_count=420),
    )
    apply_voice_realization(state, voice)  # type: ignore[arg-type]

    design = state.description.voice
    assert design.speaker is not None and design.speaker.voice_id == "voice_1"
    assert design.narration_audio is not None
    assert design.narration_audio.audio_file == "voice/narration.wav"
    assert design.alignment is not None and design.alignment.word_count == 420
    assert all(utterance.audio_segment is not None for utterance in design.utterances)
