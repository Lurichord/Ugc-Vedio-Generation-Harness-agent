"""Backfill realization data into the VideoDescription document.

Each production stage reports what it actually made; these helpers write that
realization into the description document and keep the execution tags in
sync. Controllers call them only after their critic (or deterministic quality
gate) passed, so the document never carries rejected realizations.

All functions mutate the given ProjectState in place and are no-ops when the
state predates the description document (legacy projects).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .description import (
    AlignmentSummary,
    AudioSegmentBinding,
    ClipDescription,
    Deliverable,
    NarrationAudio,
    ShotMedia,
    TimelineCaption,
    TimelineDescription,
    TimelineOverlay,
    VoiceSpeakerSpec,
    element_refs,
)
from .models import ElementStatus, ProjectState

if TYPE_CHECKING:
    from ..agents.render_agent.models import RenderArtifact
    from ..agents.timeline_agent.models import TimelineArtifact
    from ..agents.voice_agent.models import VoiceArtifact
    from .shot_asset_controller import ShotAssetArtifact
    from .shot_timeline_controller import ShotTimelineArtifact


def apply_voice_realization(state: ProjectState, voice: "VoiceArtifact") -> None:
    description = state.description
    if description is None or description.voice is None:
        return
    speaker = voice.voice_plan.speaker
    description.voice.speaker = VoiceSpeakerSpec(
        provider=speaker.provider,
        voice_id=speaker.voice_id,
        language=speaker.language,
        persona=speaker.persona,
        character_id=speaker.character_id,
    )
    description.voice.narration_audio = NarrationAudio(
        audio_file=voice.timed_audio.audio_file,
        duration_ms=voice.timed_audio.duration_ms,
        sample_rate=voice.timed_audio.sample_rate,
        channels=voice.timed_audio.channels,
    )
    description.voice.alignment = AlignmentSummary(
        coverage=voice.word_alignment.coverage,
        word_count=voice.word_alignment.word_count,
    )
    segments_by_script_id = {
        segment.script_segment_id: segment
        for segment in voice.timed_audio.segments
    }
    for utterance in description.voice.utterances:
        segment = segments_by_script_id.get(utterance.utterance_id)
        if segment is not None:
            utterance.audio_segment = AudioSegmentBinding(
                file=segment.file,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                duration_ms=segment.duration_ms,
            )
    _sync_execution_elements(state)


def apply_timeline_realization(
    state: ProjectState,
    timeline: "TimelineArtifact",
) -> None:
    """Explainer timeline: beat-keyed clips resolve to shot refs."""

    description = state.description
    if description is None:
        return
    shot_by_beat: dict[str, str] = {}
    for shot in description.shots:
        payload = shot.spec.payload
        if payload.payload_type == "explainer":
            shot_by_beat[payload.planned_beat_id] = shot.spec.shot_id
    missing = [
        clip.beat_id
        for clip in timeline.timeline.clips
        if clip.beat_id not in shot_by_beat
    ]
    if missing:
        raise ValueError(
            f"timeline clips reference beats without compiled shots: {missing}"
        )
    description.timeline = TimelineDescription(
        canvas_width=timeline.timeline.canvas_width,
        canvas_height=timeline.timeline.canvas_height,
        fps=timeline.timeline.fps,
        duration_ms=timeline.timeline.duration_ms,
        audio_file=timeline.timeline.audio_file,
        clips=[
            ClipDescription(
                clip_id=clip.clip_id,
                shot_ref=f"shot:{shot_by_beat[clip.beat_id]}",
                timeline_start_ms=clip.timeline_start_ms,
                timeline_end_ms=clip.timeline_end_ms,
                content_start_ms=clip.content_start_ms,
                content_end_ms=clip.content_end_ms,
                playback_path=clip.playback_path,
                playback_modality=clip.playback_modality,
                playback_policy=clip.playback_policy,
                audio_mode="narration_over",
                source_asset_id=clip.source_asset_id,
                derivative_id=clip.derivative_id,
            )
            for clip in timeline.timeline.clips
        ],
        captions=[
            TimelineCaption(
                cue_id=cue.cue_id,
                anchor_ref=f"beat:{cue.beat_id}",
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
            )
            for cue in timeline.captions
        ],
        overlays=[
            TimelineOverlay(
                overlay_id=overlay.overlay_id,
                anchor_ref=f"beat:{overlay.beat_id}",
                overlay_type=overlay.overlay_type,
                text=overlay.text,
                start_ms=overlay.start_ms,
                end_ms=overlay.end_ms,
            )
            for overlay in timeline.overlays
        ],
    )
    _sync_execution_elements(state)


def apply_shot_media(state: ProjectState, assets: "ShotAssetArtifact") -> None:
    description = state.description
    if description is None:
        return
    shots_by_id = {shot.spec.shot_id: shot for shot in description.shots}
    for asset in assets.assets:
        shot = shots_by_id.get(asset.shot_id)
        if shot is None:
            continue
        shot.media = ShotMedia(
            modality="ai_video",
            local_path=asset.local_path,
            sha256=asset.sha256,
            duration_ms=asset.duration_ms,
            generator_model=asset.generator_model,
            generation_prompt=asset.generation_prompt,
            generation_job_id=asset.generation_job_id,
            generation_cost_usd=asset.generation_cost_usd,
        )
        tag = state.execution.elements.get(f"shot:{asset.shot_id}")
        if tag is not None:
            tag.attempts += 1
            tag.generation_job_id = asset.generation_job_id
            tag.cost_usd = asset.generation_cost_usd
    _sync_execution_elements(state)


def apply_shot_timeline_realization(
    state: ProjectState,
    timeline: "ShotTimelineArtifact",
) -> None:
    description = state.description
    if description is None:
        return
    description.timeline = TimelineDescription(
        duration_ms=timeline.duration_ms,
        audio_file=None,
        clips=[
            ClipDescription(
                clip_id=clip.clip_id,
                shot_ref=f"shot:{clip.shot_id}",
                timeline_start_ms=clip.start_ms,
                timeline_end_ms=clip.end_ms,
                content_start_ms=0,
                content_end_ms=clip.end_ms - clip.start_ms,
                playback_path=clip.playback_path,
                playback_modality="ai_video",
                audio_mode=clip.audio_mode,
                caption=clip.caption,
            )
            for clip in timeline.clips
        ],
    )
    _sync_execution_elements(state)


def apply_render_realization(state: ProjectState, render: "RenderArtifact") -> None:
    description = state.description
    if description is None:
        return
    final = next(
        (output for output in render.outputs if output.kind == "final"),
        None,
    )
    if final is None:
        return
    if description.deliverable is None:
        description.deliverable = Deliverable()
    deliverable = description.deliverable
    deliverable.video_file = final.local_path
    deliverable.sha256 = final.sha256
    deliverable.duration_ms = final.duration_ms
    deliverable.width = final.width
    deliverable.height = final.height
    deliverable.fps = round(final.fps)
    deliverable.renderer = (
        f"{render.composition.renderer}@{render.composition.renderer_version}"
    )
    deliverable.rendered_at = render.generated_at
    _sync_execution_elements(state)


def _sync_execution_elements(state: ProjectState) -> None:
    """Tag newly realized description elements (e.g. clips) as passed."""

    assert state.description is not None
    for ref in element_refs(state.description):
        state.execution.elements.setdefault(
            ref,
            ElementStatus(status="passed", version=1),
        )
