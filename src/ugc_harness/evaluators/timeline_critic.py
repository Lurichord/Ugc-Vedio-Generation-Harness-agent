from __future__ import annotations

from pathlib import Path

from ..agents.timeline_agent.models import TimelineCandidate, TimelineQuality
from ..agents.voice_agent.models import VoiceArtifact
from ..harness.models import CriticIssue, EvaluationResult


class TimelineCritic:
    critic_id = "timeline_critic"

    def evaluate(
        self,
        project_dir: str | Path,
        voice: VoiceArtifact,
        candidate: TimelineCandidate,
        target_ref: str,
    ) -> tuple[TimelineQuality, EvaluationResult]:
        root = Path(project_dir)
        issues: list[tuple[str, str, str]] = []
        missing = 0
        for clip in candidate.timeline.clips:
            path = root / clip.playback_path
            if not path.is_file() or path.stat().st_size == 0:
                missing += 1
                issues.append((
                    "TIMELINE_MEDIA_MISSING",
                    f"{clip.clip_id} playback media is missing or empty.",
                    f"timeline_clip:{clip.beat_id}",
                ))
        clips = candidate.timeline.clips
        full_coverage = bool(
            clips
            and clips[0].timeline_start_ms == 0
            and clips[-1].timeline_end_ms == voice.timed_audio.duration_ms
            and all(
                left.timeline_end_ms == right.timeline_start_ms
                for left, right in zip(clips, clips[1:])
            )
        )
        beat_ids = [item.beat_id for item in voice.realized_beats]
        clip_beat_ids = [item.beat_id for item in clips]
        if clip_beat_ids != beat_ids:
            issues.append((
                "BEAT_CLIP_COVERAGE_INVALID",
                "Timeline clips do not match RealizedBeat order and coverage.",
                target_ref,
            ))
        if not full_coverage:
            issues.append((
                "AUDIO_COVERAGE_INVALID",
                "Timeline does not cover the complete narration audio clock.",
                target_ref,
            ))
        clip_ids = {item.clip_id for item in clips}
        transform_ids = [item.clip_id for item in candidate.visual_transforms]
        if set(transform_ids) != clip_ids or len(transform_ids) != len(clip_ids):
            issues.append((
                "TRANSFORM_COVERAGE_INVALID",
                "Each timeline clip must have exactly one visual transform.",
                target_ref,
            ))
        caption_beats = {item.beat_id for item in candidate.captions}
        if not set(beat_ids) <= caption_beats:
            issues.append((
                "CAPTION_COVERAGE_INVALID",
                "One or more beats have no caption cues.",
                target_ref,
            ))
        quality = TimelineQuality(
            passed=not issues,
            beat_count=len(beat_ids),
            clip_count=len(clips),
            caption_cue_count=len(candidate.captions),
            full_audio_coverage=full_coverage,
            missing_asset_count=missing,
            screen_derivative_count=len(candidate.derivatives),
            issues=[message for _, message, _ in issues],
        )
        evaluation = EvaluationResult(
            critic_id=self.critic_id,
            target_ref=target_ref,
            passed=quality.passed,
            issues=[
                CriticIssue(
                    issue_id=f"{self.critic_id}:{index:03d}",
                    critic_id=self.critic_id,
                    scope="timeline",
                    target_ref=issue_target,
                    severity="error",
                    code=code,
                    diagnosis=message,
                    repair_options=["recompose_beat"],
                )
                for index, (code, message, issue_target) in enumerate(issues, start=1)
            ],
        )
        return quality, evaluation
