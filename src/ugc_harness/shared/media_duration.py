"""Probe real media duration and map clocks onto it."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .video_frames import _ffmpeg_binary


@dataclass(frozen=True)
class TimeWindow:
    old_start_ms: int
    old_end_ms: int
    new_start_ms: int
    new_end_ms: int

    def map_ms(self, value: int) -> int:
        span = self.old_end_ms - self.old_start_ms
        new_span = self.new_end_ms - self.new_start_ms
        if span <= 0:
            return self.new_start_ms
        mapped = self.new_start_ms + round(
            (value - self.old_start_ms) * new_span / span
        )
        return min(max(mapped, self.new_start_ms), self.new_end_ms)

    def map_span(self, start_ms: int, end_ms: int) -> tuple[int, int]:
        start = self.map_ms(start_ms)
        end = self.map_ms(end_ms)
        if end <= start:
            end = min(self.new_end_ms, start + 1)
            if end <= start:
                start = max(self.new_start_ms, end - 1)
        return start, end


def probe_duration_ms(path: str | Path) -> int | None:
    """Return the container duration in milliseconds, or None if unreadable."""

    media = Path(path)
    if not media.is_file() or media.stat().st_size == 0:
        return None
    probe = _ffprobe_binary()
    if probe is None:
        return None
    completed = subprocess.run(
        [
            str(probe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout)
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return max(1, round(duration * 1000))


def resolve_media_duration_ms(
    path: str | Path,
    *,
    declared_ms: int | None = None,
    fallback_ms: int | None = None,
) -> int | None:
    return probe_duration_ms(path) or declared_ms or fallback_ms


def _ffprobe_binary() -> Path | None:
    try:
        ffmpeg = _ffmpeg_binary()
    except FileNotFoundError:
        ffmpeg = None
    names = ("ffprobe.exe", "ffprobe")
    checked: list[Path] = []
    if ffmpeg is not None:
        checked.append(ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"))
        checked.append(ffmpeg.with_name("ffprobe"))
        checked.append(ffmpeg.with_name("ffprobe.exe"))
    found = shutil.which("ffprobe")
    if found:
        checked.append(Path(found))
    for item in checked:
        if item.is_file():
            return item
    return None
