from __future__ import annotations

import hashlib
import json
import math
import shutil
import uuid
from pathlib import Path

from pydantic import Field

from ..shared.models import StrictModel

from ..agents.render_agent.capabilities import (
    RenderCapabilities,
    _find_remotion_binaries,
    _prepare_job_media,
    _probe_media,
    _run_checked,
    _run_streaming,
)
from ..agents.render_agent.models import (
    RenderArtifact,
    RenderCaption,
    RenderClip,
    RenderComposition,
    RenderQuality,
)
from .description_realization import apply_render_realization
from .models import ActionRecord, ProjectState, TaskBudget, TaskEnvelope, TaskScope
from .shot_timeline_controller import ShotTimelineArtifact


class ShotRenderHarnessController:
    """Renders native-audio AI video clips without a VoiceArtifact."""

    TOOL = "render.native_audio_shot_timeline"

    def __init__(self, capabilities: RenderCapabilities | None = None) -> None:
        self.capabilities = capabilities or RenderCapabilities()

    def run(
        self,
        timeline: ShotTimelineArtifact,
        project_dir: str | Path,
        state: ProjectState,
    ) -> tuple[RenderArtifact, "ShotRenderRun"]:
        if state.video.timeline_status != "passed" or state.video.render_status != "ready":
            raise ValueError("shot render stage is not ready")
        if state.video.project_id != timeline.project_id:
            raise ValueError("render inputs have different project_id values")
        root = Path(project_dir).resolve()
        renderer_dir = self.capabilities.renderer_dir
        browser = self.capabilities.browser_executable
        if browser is None:
            raise RuntimeError("Microsoft Edge was not found")
        if not (renderer_dir / "node_modules").is_dir():
            raise RuntimeError("renderer dependencies are not installed")
        fps = 30
        composition = RenderComposition(
            renderer_version="4.0.499",
            duration_ms=timeline.duration_ms,
            duration_in_frames=math.ceil(timeline.duration_ms * fps / 1000),
            audio_path=None,
            clips=[
                RenderClip(
                    clip_id=clip.clip_id,
                    beat_id=clip.shot_id,
                    start_frame=round(clip.start_ms * fps / 1000),
                    duration_in_frames=max(1, round((clip.end_ms - clip.start_ms) * fps / 1000)),
                    media_type="video",
                    media_path=clip.playback_path,
                    source_path=clip.playback_path,
                    fit_mode="cover",
                    motion_preset="native_video",
                    scale_start=1,
                    scale_end=1,
                    transition_in="none" if clip.start_ms == 0 else "hard_cut",
                    muted=not clip.preserve_source_audio,
                )
                for clip in timeline.clips
            ],
            captions=[
                RenderCaption(
                    cue_id=f"caption_{clip.shot_id}",
                    start_frame=round(clip.start_ms * fps / 1000),
                    duration_in_frames=max(1, round((clip.end_ms - clip.start_ms) * fps / 1000)),
                    text=clip.caption,
                )
                for clip in timeline.clips
                if clip.caption
            ],
            overlays=[],
        )
        missing = [clip.media_path for clip in composition.clips if not (root / clip.media_path).is_file()]
        if missing:
            raise FileNotFoundError("render media is missing: " + ", ".join(missing))
        job_id = f"{timeline.project_id}_{uuid.uuid4().hex[:10]}"
        job_dir = renderer_dir / "public" / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        video_dir = root / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        final_path = video_dir / "final.mp4"
        preview_path = video_dir / "preview.mp4"
        try:
            props = _prepare_job_media(root, job_dir, composition)
            props_path = job_dir / "props.json"
            props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
            _run_streaming([
                "node", str(renderer_dir / "render.mjs"), str(props_path),
                str(final_path), str(job_dir), str(browser),
            ], cwd=renderer_dir)
            ffmpeg, ffprobe = _find_remotion_binaries(renderer_dir)
            _run_checked([
                str(ffmpeg), "-y", "-i", str(final_path), "-vf", "scale=540:960",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                str(preview_path),
            ], cwd=root)
            outputs = [
                _probe_media(final_path, root, ffprobe, "final"),
                _probe_media(preview_path, root, ffprobe, "preview"),
            ]
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
        final = outputs[0]
        delta = abs(final.duration_ms - timeline.duration_ms)
        resolution_correct = final.width == 1080 and final.height == 1920
        fps_correct = abs(final.fps - 30) < 0.01
        quality = RenderQuality(
            passed=(
                final.has_video
                and final.has_audio
                and delta <= 34
                and resolution_correct
                and fps_correct
            ),
            expected_duration_ms=timeline.duration_ms,
            actual_duration_ms=final.duration_ms,
            duration_delta_ms=delta,
            max_allowed_delta_ms=34,
            resolution_correct=resolution_correct,
            fps_correct=fps_correct,
            audio_present=final.has_audio,
            video_present=final.has_video,
            full_timeline_coverage=delta <= 34,
            missing_media_count=0,
            issues=[] if final.has_audio else ["native clip audio is missing"],
        )
        artifact = RenderArtifact(
            project_id=timeline.project_id,
            source_voice=None,
            source_timeline="shot_timeline_artifact.json",
            composition=composition,
            outputs=outputs,
            quality=quality,
        )
        task = TaskEnvelope(
            task_id=f"task_shot_render_{timeline.project_id}_v{state.video.state_version}",
            agent="render_stage",
            goal="Render the Shot timeline while preserving each AI video's native audio.",
            scope=TaskScope(project_id=timeline.project_id, shot_ids=[c.shot_id for c in timeline.clips]),
            based_on_state_version=state.video.state_version,
            format_id=timeline.format_id,
            allowed_tools=[self.TOOL],
            required_outputs=["render_artifact"],
            forbidden_actions=["mute_source_audio", "replace_assets"],
            budget=TaskBudget(max_steps=2, max_retries=0),
            input_hash=hashlib.sha256(timeline.model_dump_json().encode()).hexdigest(),
        )
        action = ActionRecord(
            action_id=f"action_{uuid.uuid4().hex[:12]}", agent="render_stage",
            task_id=task.task_id, tool=self.TOOL,
            result="success" if quality.passed else "failed",
            reason="rendered native-audio AI video timeline",
        )
        next_state = state.model_copy(deep=True)
        next_state.video.state_version += 1
        next_state.video.render_status = "passed" if quality.passed else "needs_revision"
        if quality.passed:
            apply_render_realization(next_state, artifact)
        return artifact, ShotRenderRun(
            task=task,
            actions=[action],
            committed_state_version=next_state.video.state_version,
            project_state=next_state,
        )


class ShotRenderRun(StrictModel):
    task: TaskEnvelope
    actions: list[ActionRecord]
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState
