from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def ffmpeg_bin() -> Path:
    raw = os.environ.get("FFMPEG_PATH", "").strip().strip("\"'")
    if raw:
        path = Path(raw)
        if path.is_dir():
            candidate = path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if candidate.is_file():
                return candidate
        elif path.is_file():
            return path
        raise FileNotFoundError(f"FFMPEG_PATH not found: {raw}")
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise ValueError("FFMPEG_PATH is missing and ffmpeg is not on PATH")


def ffprobe_bin() -> Path:
    ffmpeg = ffmpeg_bin()
    name = "ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"
    sibling = ffmpeg.with_name(name)
    if sibling.is_file():
        return sibling
    found = shutil.which("ffprobe")
    if found:
        return Path(found)
    raise FileNotFoundError(f"ffprobe not found next to {ffmpeg}")


def run_ffmpeg(args: list[str], *, timeout: int = 600) -> None:
    command = [str(ffmpeg_bin()), "-hide_banner", "-loglevel", "error", "-y", *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip()
        raise RuntimeError(detail)


def probe_duration(video_path: str | Path) -> float:
    stream = _probe_seconds(
        video_path,
        ["-select_streams", "v:0", "-show_entries", "stream=duration"],
    )
    if stream is not None:
        return stream
    container = _probe_seconds(
        video_path,
        ["-show_entries", "format=duration"],
    )
    if container is not None:
        return container
    raise RuntimeError(f"ffprobe could not read duration: {video_path}")


def _probe_seconds(video_path: str | Path, extra: list[str]) -> float | None:
    command = [
        str(ffprobe_bin()),
        "-v",
        "error",
        *extra,
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffprobe failed").strip()
        raise RuntimeError(detail)
    return _parse_seconds(completed.stdout)


def _parse_seconds(raw: str) -> float | None:
    text = raw.strip().splitlines()[-1] if raw.strip() else ""
    if not text or text.upper() == "N/A":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def frame_times(duration: float, count: int, at: str) -> list[float]:
    if duration <= 0:
        raise ValueError("video duration must be positive")
    if count < 1:
        raise ValueError("count must be at least 1")
    last = max(duration - 0.05, 0.0)
    if at == "start":
        return [0.0]
    if at == "end":
        return [last]
    if at == "even":
        if count == 1:
            return [min(duration / 2, last)]
        times = [duration * i / (count - 1) for i in range(count)]
        times[-1] = last
        return times
    seconds = float(at)
    if seconds < 0:
        raise ValueError("at must be >= 0")
    return [min(seconds, last)]
