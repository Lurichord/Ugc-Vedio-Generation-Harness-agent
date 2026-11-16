from __future__ import annotations

from pathlib import Path

from ..ark import unique_path
from ..ffmpeg import frame_times, probe_duration, run_ffmpeg


def resolve_at(count: int, at: str | None) -> str:
    if at:
        return at
    return "even" if count > 1 else "end"


def extract_frames(
    video_path: str,
    directory: Path,
    *,
    count: int = 1,
    at: str | None = None,
) -> list[Path]:
    source = Path(video_path)
    if not source.is_file():
        raise FileNotFoundError(video_path)
    if count < 1 or count > 10:
        raise ValueError("count must be between 1 and 10")
    duration = probe_duration(source)
    times = frame_times(duration, count, resolve_at(count, at))
    paths: list[Path] = []
    for moment in times:
        output = unique_path(directory, ".png")
        run_ffmpeg(
            [
                "-ss",
                f"{moment:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(output),
            ]
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg did not write frame at {moment:.3f}s")
        paths.append(output)
    return paths
