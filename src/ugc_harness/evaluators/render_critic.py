from __future__ import annotations

import math
from pathlib import Path

from ..agents.render_agent.models import RenderCandidate, RenderQuality
from ..agents.timeline_agent.models import TimelineArtifact
from ..harness.models import CriticIssue, EvaluationResult


class RenderCritic:
    critic_id = "render_critic"

    def evaluate(
        self,
        project_dir: str | Path,
        timeline: TimelineArtifact,
        candidate: RenderCandidate,
        target_ref: str,
    ) -> tuple[RenderQuality, EvaluationResult]:
        issues: list[tuple[str, str, str]] = []
        root = Path(project_dir)
        outputs = {item.kind: item for item in candidate.outputs}
        final = outputs.get("final")
        missing = 0
        for kind in ("final", "preview"):
            output = outputs.get(kind)
            output_path = root / output.local_path if output else None
            if (
                output is None
                or output_path is None
                or not output_path.is_file()
                or output_path.stat().st_size == 0
            ):
                missing += 1
                issues.append((
                    "RENDER_OUTPUT_MISSING",
                    f"Required {kind} render output is missing.",
                    f"rendered_media:{kind}",
                ))
        if final is None:
            expected = candidate.composition.duration_ms
            actual = expected
            delta = 0
            resolution_correct = fps_correct = audio_present = video_present = False
        else:
            expected = candidate.composition.duration_ms
            actual = final.duration_ms
            delta = abs(actual - expected)
            resolution_correct = final.width == 1080 and final.height == 1920
            fps_correct = abs(final.fps - 30) < 0.01
            audio_present = final.has_audio
            video_present = final.has_video
            checks = [
                (delta <= math.ceil(1000 / 30), "RENDER_DURATION_INVALID", f"Render duration differs from audio by {delta}ms."),
                (resolution_correct, "RENDER_RESOLUTION_INVALID", "Final render is not 1080x1920."),
                (fps_correct, "RENDER_FPS_INVALID", "Final render is not 30fps."),
                (audio_present, "RENDER_AUDIO_MISSING", "Final render has no audio stream."),
                (video_present, "RENDER_VIDEO_MISSING", "Final render has no video stream."),
                (final.video_codec == "h264", "RENDER_VIDEO_CODEC_INVALID", "Final render video codec is not H.264."),
                (final.audio_codec == "aac", "RENDER_AUDIO_CODEC_INVALID", "Final render audio codec is not AAC."),
            ]
            issues.extend((code, message, "rendered_media:final") for passed, code, message in checks if not passed)
        if not timeline.quality.full_audio_coverage:
            issues.append(("TIMELINE_COVERAGE_INVALID", "Approved timeline does not cover the full audio.", "artifact:timeline"))
        preview = outputs.get("preview")
        if preview and (preview.width, preview.height) != (540, 960):
            issues.append((
                "PREVIEW_RESOLUTION_INVALID",
                "Preview render is not 540x960.",
                "rendered_media:preview",
            ))
        max_delta = math.ceil(1000 / 30)
        quality = RenderQuality(
            passed=not issues,
            expected_duration_ms=expected,
            actual_duration_ms=actual,
            duration_delta_ms=delta,
            max_allowed_delta_ms=max_delta,
            resolution_correct=resolution_correct,
            fps_correct=fps_correct,
            audio_present=audio_present,
            video_present=video_present,
            full_timeline_coverage=timeline.quality.full_audio_coverage,
            missing_media_count=missing,
            issues=[message for _, message, _ in issues],
        )
        return quality, EvaluationResult(
            critic_id=self.critic_id,
            target_ref=target_ref,
            passed=quality.passed,
            issues=[
                CriticIssue(
                    issue_id=f"{self.critic_id}:{index:03d}",
                    critic_id=self.critic_id,
                    scope="render",
                    target_ref=issue_target,
                    severity="error",
                    code=code,
                    diagnosis=message,
                    repair_options=["rerender"],
                )
                for index, (code, message, issue_target) in enumerate(issues, start=1)
            ],
        )
