from __future__ import annotations

from pathlib import Path

from ..ark import unique_path, write_unique_file
from ..ffmpeg import run_ffmpeg


def _concat_line(path: Path) -> str:
    posix = path.resolve().as_posix().replace("'", r"'\''")
    return f"file '{posix}'"


def merge_videos(video_paths: list[str], directory: Path) -> Path:
    if len(video_paths) < 1:
        raise ValueError("video_paths is empty")
    sources = [Path(path) for path in video_paths]
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    if len(sources) == 1:
        return write_unique_file(directory, ".mp4", sources[0].read_bytes())
    output = unique_path(directory, ".mp4")
    listing = output.with_suffix(".concat.txt")
    listing.write_text(
        "\n".join(_concat_line(path) for path in sources) + "\n",
        encoding="utf-8",
    )
    try:
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
    finally:
        listing.unlink(missing_ok=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("ffmpeg did not write merged video")
    return output
