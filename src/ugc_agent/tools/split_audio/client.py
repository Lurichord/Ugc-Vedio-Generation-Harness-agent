from __future__ import annotations

from pathlib import Path

from ..ark import unique_path
from ..ffmpeg import run_ffmpeg


def split_audio_file(video_path: str, directory: Path) -> Path:
    source = Path(video_path)
    if not source.is_file():
        raise FileNotFoundError(video_path)
    output = unique_path(directory, ".mp3")
    run_ffmpeg(
        [
            "-i",
            str(source),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            str(output),
        ]
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("ffmpeg did not write audio")
    return output
