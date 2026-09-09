"""Extract a still frame from a generated shot video."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .settings import load_shell_env

_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})
_REPO_ROOT = Path(__file__).resolve().parents[3]


def extract_last_frame(video: Path, output: Path) -> Path:
    """Write the last frame of ``video`` to ``output`` using ffmpeg."""

    if not video.is_file():
        raise FileNotFoundError(f"video does not exist: {video}")
    if video.suffix.lower() not in _VIDEO_SUFFIXES:
        raise ValueError(
            f"refusing to extract a frame from a non-video file: {video}"
        )
    ffmpeg = _ffmpeg_binary()
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-sseof",
            "-0.05",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(output),
        ],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not output.is_file():
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"failed to extract last frame from {video} "
            f"(ffmpeg exit code {completed.returncode}): "
            f"{stderr[-400:] or 'no stderr output'}"
        )
    return output


def _ffmpeg_binary() -> Path:
    configured = _configured_ffmpeg()
    if configured is not None:
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    bundled = (
        _REPO_ROOT
        / "renderer"
        / "node_modules"
        / "@remotion"
        / "compositor-win32-x64-msvc"
        / "ffmpeg.exe"
    )
    if bundled.is_file():
        return bundled
    raise FileNotFoundError("ffmpeg is not available")


def _configured_ffmpeg() -> Path | None:
    raw = os.getenv("FFMPEG_PATH")
    if not raw:
        for candidate in (Path(".env"), _REPO_ROOT / ".env"):
            if candidate.is_file():
                raw = load_shell_env(candidate).get("FFMPEG_PATH")
                if raw:
                    break
    if not raw:
        return None
    return _resolve_ffmpeg_path(raw)


def _resolve_ffmpeg_path(raw: str) -> Path | None:
    path = Path(raw.strip().strip('"').strip("'"))
    names = ("ffmpeg.exe", "ffmpeg")
    checked: list[Path] = []
    if path.is_file():
        checked.append(path)
    if path.is_dir():
        checked.extend(path / name for name in names)
        checked.extend(path / "bin" / name for name in names)
        checked.extend(path.parent / "bin" / name for name in names)
    for item in checked:
        if item.is_file():
            return item
    return None
