from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from ...shared.media_duration import TimeWindow, resolve_media_duration_ms
from ..asset_agent.models import VIDEO_FILE_SUFFIXES, AssetArtifact, AssetCard
from ..editorial_agent.models import EditorialArtifact, ExplorationDirection
from ..voice_agent.audio import fit_wav_to_windows, force_wav_duration
from ..voice_agent.models import RealizedBeat, VoiceArtifact, WordTimestamp
from .models import (
    CaptionCue,
    DerivedAsset,
    OverlayCue,
    TimelineClip,
    TimelinePlan,
    TimelineCandidate,
    VisualTransform,
)


class ScreenAnimationProvider(Protocol):
    def generate(
        self,
        *,
        asset: AssetCard,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
    ) -> DerivedAsset: ...


class TimelineCapabilities:
    def __init__(self, screen_animation_provider: ScreenAnimationProvider):
        self.screen_animation_provider = screen_animation_provider

    def run(
        self,
        voice: VoiceArtifact,
        editorial: EditorialArtifact,
        assets_artifact: AssetArtifact,
        project_dir: str | Path,
    ) -> TimelineCandidate:
        root = Path(project_dir)
        if not (
            voice.project_id
            == editorial.project_id
            == assets_artifact.project_id
        ):
            raise ValueError("stage project_id values do not match")

        assets = {asset.asset_id: asset for asset in assets_artifact.assets}
        prepared_images = {
            image.asset_id: image for image in assets_artifact.prepared_images
        }
        resolutions = {
            item.visual_request_id: item for item in assets_artifact.resolutions
        }
        visuals_by_beat = {
            item.beat_id: item
            for item in editorial.editorial_plan.visual_requirements
        }
        claims_by_beat: dict[str, list[str]] = {}
        for claim in editorial.editorial_plan.claims:
            if claim.claim_type == "interpretation":
                claims_by_beat.setdefault(claim.beat_id, []).append(claim.statement)

        derivatives: list[DerivedAsset] = []
        clips: list[TimelineClip] = []
        transforms: list[VisualTransform] = []
        overlays: list[OverlayCue] = []
        beats = voice.realized_beats
        prepared: list[dict[str, object]] = []

        for index, beat in enumerate(beats):
            visual = visuals_by_beat.get(beat.beat_id)
            if visual is None:
                raise ValueError(f"missing visual requirement for {beat.beat_id}")
            resolution = resolutions.get(visual.visual_request_id)
            if resolution is None or resolution.status != "resolved":
                raise ValueError(
                    f"visual is unresolved: {visual.visual_request_id}"
                )
            asset = assets.get(str(resolution.asset_id))
            if asset is None:
                raise ValueError(f"missing selected asset: {resolution.asset_id}")
            direction = next(
                (
                    item
                    for item in visual.directions
                    if item.direction_id == resolution.selected_direction_id
                ),
                None,
            )
            if direction is None:
                raise ValueError(
                    f"selected direction is missing: {resolution.selected_direction_id}"
                )

            derivative: DerivedAsset | None = None
            if direction.asset_type == "screen_recording":
                derivative = self.screen_animation_provider.generate(
                    asset=asset,
                    beat=beat,
                    direction=direction,
                    project_dir=root,
                )
                derivatives.append(derivative)

            old_start = beat.start_ms if index else 0
            old_end = (
                beats[index + 1].start_ms
                if index + 1 < len(beats)
                else voice.timed_audio.duration_ms
            )
            prepared_image = prepared_images.get(asset.asset_id)
            playback_path = (
                derivative.local_path
                if derivative
                else prepared_image.output_path
                if prepared_image
                else asset.local_path
            )
            is_video = (
                derivative is not None
                or str(asset.mime_type).startswith("video/")
                or Path(playback_path).suffix.lower() in VIDEO_FILE_SUFFIXES
            )
            old_ms = max(1, old_end - old_start)
            if is_video:
                declared = (
                    derivative.duration_ms
                    if derivative is not None and derivative.duration_ms
                    else asset.duration_ms
                )
                target_ms = resolve_media_duration_ms(
                    root / playback_path,
                    declared_ms=declared,
                    fallback_ms=old_ms,
                ) or old_ms
            else:
                target_ms = old_ms
            prepared.append(
                {
                    "beat": beat,
                    "visual": visual,
                    "asset": asset,
                    "direction": direction,
                    "derivative": derivative,
                    "playback_path": playback_path,
                    "is_video": is_video,
                    "old_start": old_start,
                    "old_end": old_end,
                    "target_ms": max(1, int(target_ms)),
                }
            )

        windows: list[TimeWindow] = []
        cursor = 0
        for item in prepared:
            target_ms = int(item["target_ms"])
            windows.append(
                TimeWindow(
                    old_start_ms=int(item["old_start"]),
                    old_end_ms=int(item["old_end"]),
                    new_start_ms=cursor,
                    new_end_ms=cursor + target_ms,
                )
            )
            cursor += target_ms

        audio_file = voice.timed_audio.audio_file
        if any(
            window.new_end_ms - window.new_start_ms
            != window.old_end_ms - window.old_start_ms
            for window in windows
        ):
            aligned = root / "audio" / "narration_aligned.wav"
            fit_wav_to_windows(
                root / voice.timed_audio.audio_file,
                [
                    (window.old_start_ms, window.old_end_ms, window.new_end_ms - window.new_start_ms)
                    for window in windows
                ],
                aligned,
            )
            force_wav_duration(aligned, cursor)
            audio_file = aligned.relative_to(root).as_posix()

        scaled_beats = [
            _scale_beat(item["beat"], window)
            for item, window in zip(prepared, windows)
        ]
        window_by_planned = {
            str(item["beat"].planned_beat_id): window
            for item, window in zip(prepared, windows)
        }
        scaled_words = [
            _scale_word(word, window_by_planned.get(word.planned_beat_id))
            for word in voice.word_alignment.words
        ]

        for item, window, beat in zip(prepared, windows, scaled_beats):
            clip_id = f"clip_{len(clips) + 1:02d}"
            is_video = bool(item["is_video"])
            content_start, content_end = window.map_span(
                int(item["beat"].start_ms), int(item["beat"].end_ms)
            )
            clips.append(
                TimelineClip(
                    clip_id=clip_id,
                    beat_id=beat.beat_id,
                    visual_request_id=item["visual"].visual_request_id,
                    source_asset_id=item["asset"].asset_id,
                    derivative_id=(
                        item["derivative"].derivative_id
                        if item["derivative"] is not None
                        else None
                    ),
                    timeline_start_ms=window.new_start_ms,
                    timeline_end_ms=window.new_end_ms,
                    content_start_ms=content_start,
                    content_end_ms=content_end,
                    playback_path=str(item["playback_path"]),
                    playback_modality="video" if is_video else "image",
                    playback_policy=(
                        "fit_narration_to_video" if is_video else "hold_to_audio"
                    ),
                    transition_in=(
                        "none"
                        if not clips
                        else (
                            "punch_cut"
                            if beat.discourse_role in {"question", "reveal", "contrast"}
                            else "hard_cut"
                        )
                    ),
                    preserve_source_audio=item["asset"].modality == "talking_head",
                )
            )
            transforms.append(
                _build_transform(
                    clip_id,
                    item["asset"],
                    item["direction"],
                    item["derivative"] is not None,
                )
            )
            overlays.extend(
                _build_overlays(
                    beat,
                    item["asset"],
                    claims_by_beat.get(beat.beat_id, []),
                )
            )

        captions = _build_captions(
            scaled_beats,
            scaled_words,
        )
        timeline = TimelinePlan(
            audio_file=audio_file,
            duration_ms=cursor,
            clips=clips,
        )
        return TimelineCandidate(
            project_id=voice.project_id,
            derivatives=derivatives,
            timeline=timeline,
            captions=captions,
            visual_transforms=transforms,
            overlays=overlays,
        )


def _scale_beat(beat: RealizedBeat, window: TimeWindow) -> RealizedBeat:
    start, end = window.map_span(beat.start_ms, beat.end_ms)
    return beat.model_copy(
        update={
            "start_ms": start,
            "end_ms": end,
            "duration_ms": end - start,
        }
    )


def _scale_word(
    word: WordTimestamp,
    window: TimeWindow | None,
) -> WordTimestamp:
    if window is None:
        return word
    start, end = window.map_span(word.start_ms, word.end_ms)
    return word.model_copy(update={"start_ms": start, "end_ms": end})


def _build_transform(
    clip_id: str,
    asset: AssetCard,
    direction: ExplorationDirection,
    is_screen_derivative: bool,
) -> VisualTransform:
    if is_screen_derivative:
        return VisualTransform(
            clip_id=clip_id,
            fit_mode="cover",
            motion_preset="ai_screen_motion",
            scale_start=1,
            scale_end=1,
        )
    if asset.modality == "ai_video":
        return VisualTransform(
            clip_id=clip_id,
            fit_mode="cover",
            motion_preset="native_video",
            scale_start=1,
            scale_end=1,
        )
    if direction.asset_type in {
        "source_screenshot",
        "document_screenshot",
        "chart",
    }:
        return VisualTransform(
            clip_id=clip_id,
            fit_mode="contain",
            motion_preset="document_focus",
            scale_start=1,
            scale_end=1.06,
        )
    return VisualTransform(
        clip_id=clip_id,
        fit_mode="cover",
        motion_preset=(
            "slow_pan" if direction.visual_role in {"context", "emotion"} else "subtle_push"
        ),
        scale_start=1,
        scale_end=1.08,
    )


def _build_overlays(
    beat: RealizedBeat,
    asset: AssetCard,
    interpretations: list[str],
) -> list[OverlayCue]:
    result: list[OverlayCue] = []
    short_end = min(beat.end_ms, beat.start_ms + 2_000)
    if asset.source is not None:
        parsed = urlparse(asset.source.source_url)
        source_name = asset.source.publisher or parsed.hostname or "Web 来源"
        result.append(
            OverlayCue(
                overlay_id=f"overlay_{beat.beat_id}_source",
                beat_id=beat.beat_id,
                overlay_type="source_attribution",
                text=f"来源：{source_name}",
                start_ms=beat.start_ms,
                end_ms=min(beat.end_ms, beat.start_ms + 2_500),
                position="bottom_left",
            )
        )
    if interpretations:
        result.append(
            OverlayCue(
                overlay_id=f"overlay_{beat.beat_id}_interpretation",
                beat_id=beat.beat_id,
                overlay_type="interpretation_label",
                text="观点 / 推断",
                start_ms=beat.start_ms,
                end_ms=short_end,
                position="top_left",
            )
        )
    return result


def _build_captions(
    beats: list[RealizedBeat],
    words: list[WordTimestamp],
) -> list[CaptionCue]:
    by_id = {word.word_id: word for word in words}
    cues: list[CaptionCue] = []
    for beat in beats:
        selected = [by_id[word_id] for word_id in beat.word_ids if word_id in by_id]
        bucket: list[WordTimestamp] = []
        for word in selected:
            bucket.append(word)
            text = "".join(item.word for item in bucket)
            elapsed = bucket[-1].end_ms - bucket[0].start_ms
            punctuation = bool(_CAPTION_BREAK.search(word.word))
            if (
                (punctuation and len(text) >= 6)
                or len(text) >= 14
                or elapsed >= 2_200
            ):
                cues.append(_caption_cue(len(cues) + 1, beat.beat_id, bucket))
                bucket = []
        if bucket:
            cues.append(_caption_cue(len(cues) + 1, beat.beat_id, bucket))
    return cues


def _caption_cue(
    index: int,
    beat_id: str,
    words: list[WordTimestamp],
) -> CaptionCue:
    return CaptionCue(
        cue_id=f"caption_{index:03d}",
        beat_id=beat_id,
        start_ms=words[0].start_ms,
        end_ms=words[-1].end_ms,
        text="".join(item.word for item in words),
        word_ids=[item.word_id for item in words],
    )


_CAPTION_BREAK = re.compile(r"[，。！？；：,.!?;:]$")
