from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from ..asset_agent.models import AssetArtifact, AssetCard
from ..editorial_agent.models import EditorialArtifact, ExplorationDirection
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

            clip_start = beat.start_ms if index else 0
            clip_end = (
                beats[index + 1].start_ms
                if index + 1 < len(beats)
                else voice.timed_audio.duration_ms
            )
            prepared = prepared_images.get(asset.asset_id)
            playback_path = (
                derivative.local_path
                if derivative
                else prepared.output_path
                if prepared
                else asset.local_path
            )
            is_video = (
                derivative is not None
                or asset.mime_type.startswith("video/")
                or asset.modality == "ai_video"
            )
            clip_id = f"clip_{index + 1:02d}"
            clips.append(
                TimelineClip(
                    clip_id=clip_id,
                    beat_id=beat.beat_id,
                    visual_request_id=visual.visual_request_id,
                    source_asset_id=asset.asset_id,
                    derivative_id=(
                        derivative.derivative_id if derivative else None
                    ),
                    timeline_start_ms=clip_start,
                    timeline_end_ms=clip_end,
                    content_start_ms=beat.start_ms,
                    content_end_ms=beat.end_ms,
                    playback_path=playback_path,
                    playback_modality="video" if is_video else "image",
                    playback_policy=(
                        "trim_or_loop_to_audio" if is_video else "hold_to_audio"
                    ),
                    transition_in=(
                        "none"
                        if index == 0
                        else (
                            "punch_cut"
                            if beat.discourse_role in {"question", "reveal", "contrast"}
                            else "hard_cut"
                        )
                    ),
                )
            )
            transforms.append(
                _build_transform(clip_id, asset, direction, derivative is not None)
            )
            overlays.extend(
                _build_overlays(
                    beat,
                    asset,
                    direction,
                    claims_by_beat.get(beat.beat_id, []),
                )
            )

        captions = _build_captions(
            beats,
            voice.word_alignment.words,
        )
        timeline = TimelinePlan(
            audio_file=voice.timed_audio.audio_file,
            duration_ms=voice.timed_audio.duration_ms,
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
    direction: ExplorationDirection,
    interpretations: list[str],
) -> list[OverlayCue]:
    result: list[OverlayCue] = []
    short_end = min(beat.end_ms, beat.start_ms + 2_000)
    if asset.generated_media_disclosure_required or direction.asset_type in {
        "ai_image",
        "ai_video",
        "screen_recording",
    }:
        result.append(
            OverlayCue(
                overlay_id=f"overlay_{beat.beat_id}_ai",
                beat_id=beat.beat_id,
                overlay_type="generated_media_disclosure",
                text="AI 生成画面",
                start_ms=beat.start_ms,
                end_ms=short_end,
                position="top_right",
            )
        )
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
