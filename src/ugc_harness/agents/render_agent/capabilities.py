from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path

from ..timeline_agent.models import TimelineArtifact
from ..voice_agent.models import VoiceArtifact
from .models import (
    RenderCaption,
    RenderClip,
    RenderComposition,
    RenderOverlay,
    RenderCandidate,
    RenderedMedia,
)


class RenderCapabilities:
    def __init__(
        self,
        *,
        renderer_dir: str | Path | None = None,
        browser_executable: str | Path | None = None,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[4]
        self.renderer_dir = (
            Path(renderer_dir)
            if renderer_dir is not None
            else repository_root / "renderer"
        )
        self.browser_executable = (
            Path(browser_executable)
            if browser_executable is not None
            else _find_edge()
        )

    def run(
        self,
        voice: VoiceArtifact,
        timeline_artifact: TimelineArtifact,
        project_dir: str | Path,
    ) -> RenderCandidate:
        root = Path(project_dir).resolve()
        if voice.project_id != timeline_artifact.project_id:
            raise ValueError("stage project_id values do not match")
        if self.browser_executable is None:
            raise RuntimeError("Microsoft Edge was not found")
        if not (self.renderer_dir / "node_modules").is_dir():
            raise RuntimeError("renderer dependencies are not installed")

        composition, missing = _build_composition(
            root,
            voice,
            timeline_artifact,
        )
        if missing:
            raise FileNotFoundError(
                "render media is missing: " + ", ".join(missing)
            )

        job_id = f"{voice.project_id}_{uuid.uuid4().hex[:10]}"
        job_dir = self.renderer_dir / "public" / "jobs" / job_id
        if self.renderer_dir not in job_dir.parents:
            raise RuntimeError("invalid renderer job directory")
        job_dir.mkdir(parents=True, exist_ok=False)
        video_dir = root / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        final_path = video_dir / "final.mp4"
        preview_path = video_dir / "preview.mp4"
        props_path = job_dir / "props.json"

        try:
            render_props = _prepare_job_media(
                root,
                job_dir,
                composition,
            )
            props_path.write_text(
                json.dumps(render_props, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _run_streaming(
                [
                    "node",
                    str(self.renderer_dir / "render.mjs"),
                    str(props_path),
                    str(final_path),
                    str(job_dir),
                    str(self.browser_executable),
                ],
                cwd=self.renderer_dir,
            )
            ffmpeg, ffprobe = _find_remotion_binaries(self.renderer_dir)
            _run_checked(
                [
                    str(ffmpeg),
                    "-y",
                    "-i",
                    str(final_path),
                    "-vf",
                    "scale=540:960",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "28",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-movflags",
                    "+faststart",
                    str(preview_path),
                ],
                cwd=root,
            )
            final_media = _probe_media(
                final_path,
                root,
                ffprobe,
                "final",
            )
            preview_media = _probe_media(
                preview_path,
                root,
                ffprobe,
                "preview",
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

        return RenderCandidate(
            project_id=voice.project_id,
            composition=composition,
            outputs=[final_media, preview_media],
        )


def _build_composition(
    project_dir: Path,
    voice: VoiceArtifact,
    timeline_artifact: TimelineArtifact,
) -> tuple[RenderComposition, list[str]]:
    fps = 30
    transforms = {
        item.clip_id: item for item in timeline_artifact.visual_transforms
    }
    clips: list[RenderClip] = []
    missing: list[str] = []
    for item in timeline_artifact.timeline.clips:
        transform = transforms[item.clip_id]
        selected_path = item.playback_path
        if not (project_dir / selected_path).is_file():
            missing.append(selected_path)
        start_frame = _ms_to_frame(item.timeline_start_ms, fps)
        end_frame = _ms_to_frame(item.timeline_end_ms, fps)
        clips.append(
            RenderClip(
                clip_id=item.clip_id,
                beat_id=item.beat_id,
                start_frame=start_frame,
                duration_in_frames=max(1, end_frame - start_frame),
                media_type=item.playback_modality,
                media_path=selected_path,
                source_path=item.playback_path,
                fit_mode=transform.fit_mode,
                motion_preset=transform.motion_preset,
                scale_start=transform.scale_start,
                scale_end=transform.scale_end,
                transition_in=item.transition_in,
            )
        )

    captions = [
        RenderCaption(
            cue_id=item.cue_id,
            start_frame=_ms_to_frame(item.start_ms, fps),
            duration_in_frames=max(
                1,
                _ms_to_frame(item.end_ms, fps)
                - _ms_to_frame(item.start_ms, fps),
            ),
            text=item.text,
        )
        for item in timeline_artifact.captions
    ]
    overlays = [
        RenderOverlay(
            overlay_id=item.overlay_id,
            overlay_type=item.overlay_type,
            text=item.text,
            start_frame=_ms_to_frame(item.start_ms, fps),
            duration_in_frames=max(
                1,
                _ms_to_frame(item.end_ms, fps)
                - _ms_to_frame(item.start_ms, fps),
            ),
            position=item.position,
        )
        for item in timeline_artifact.overlays
    ]
    audio_path = voice.timed_audio.audio_file
    if not (project_dir / audio_path).is_file():
        missing.append(audio_path)
    return (
        RenderComposition(
            renderer_version="4.0.499",
            duration_ms=voice.timed_audio.duration_ms,
            duration_in_frames=math.ceil(
                voice.timed_audio.duration_ms * fps / 1000
            ),
            audio_path=audio_path,
            clips=clips,
            captions=captions,
            overlays=overlays,
        ),
        missing,
    )


def _prepare_job_media(
    project_dir: Path,
    job_dir: Path,
    composition: RenderComposition,
) -> dict:
    payload = composition.model_dump(mode="json")
    media_dir = job_dir / "media"
    audio_dir = job_dir / "audio"
    media_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    for payload_clip, clip in zip(payload["clips"], composition.clips):
        suffix = Path(clip.media_path).suffix.lower()
        destination = media_dir / f"{clip.clip_id}{suffix}"
        shutil.copy2(project_dir / clip.media_path, destination)
        payload_clip["media_path"] = destination.relative_to(job_dir).as_posix()
    if composition.audio_path is not None:
        audio_destination = audio_dir / "narration.wav"
        shutil.copy2(project_dir / composition.audio_path, audio_destination)
        payload["audio_path"] = audio_destination.relative_to(job_dir).as_posix()
    return payload


def _run_streaming(command: list[str], *, cwd: Path) -> None:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    lines: list[str] = []
    for line in process.stdout:
        clean = line.rstrip()
        lines.append(clean)
        print(clean, flush=True)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(
            "Remotion render failed: " + "\n".join(lines[-20:])
        )


def _run_checked(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=False,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raw = completed.stderr or completed.stdout or b"unknown error"
        raise RuntimeError(raw.decode("utf-8", errors="replace")[-1000:])


def _find_remotion_binaries(renderer_dir: Path) -> tuple[Path, Path]:
    compositor = (
        renderer_dir
        / "node_modules"
        / "@remotion"
        / "compositor-win32-x64-msvc"
    )
    ffmpeg = compositor / "ffmpeg.exe"
    ffprobe = compositor / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise FileNotFoundError("Remotion FFmpeg binaries are missing")
    return ffmpeg, ffprobe


def _probe_media(
    path: Path,
    project_dir: Path,
    ffprobe: Path,
    kind: str,
) -> RenderedMedia:
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    value = json.loads(completed.stdout)
    video = next(
        stream for stream in value["streams"] if stream["codec_type"] == "video"
    )
    audio = next(
        (
            stream
            for stream in value["streams"]
            if stream["codec_type"] == "audio"
        ),
        None,
    )
    fps = _fraction(video.get("avg_frame_rate") or video["r_frame_rate"])
    container_duration_ms = round(float(value["format"]["duration"]) * 1000)
    video_duration_ms = round(
        float(video.get("duration") or value["format"]["duration"]) * 1000
    )
    audio_duration_ms = (
        round(float(audio["duration"]) * 1000)
        if audio is not None and audio.get("duration")
        else None
    )
    raw = path.read_bytes()
    return RenderedMedia(
        kind=kind,
        local_path=path.relative_to(project_dir).as_posix(),
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        duration_ms=video_duration_ms,
        video_duration_ms=video_duration_ms,
        audio_duration_ms=audio_duration_ms,
        container_duration_ms=container_duration_ms,
        video_codec=str(video["codec_name"]),
        audio_codec=str(audio["codec_name"]) if audio else "none",
        has_video=True,
        has_audio=audio is not None,
    )


def _ms_to_frame(milliseconds: int, fps: int) -> int:
    return round(milliseconds * fps / 1000)


def _fraction(value: str) -> float:
    numerator, denominator = value.split("/", 1)
    return float(numerator) / float(denominator)


def _find_edge() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((item for item in candidates if item.is_file()), None)
