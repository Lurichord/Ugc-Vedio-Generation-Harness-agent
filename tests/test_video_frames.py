from pathlib import Path

from ugc_harness.shared.video_frames import _resolve_ffmpeg_path


def test_resolve_ffmpeg_path_accepts_exe_and_bin_dir(tmp_path: Path) -> None:
    exe = tmp_path / "bin" / "ffmpeg.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"ff")
    (tmp_path / "lib").mkdir()
    assert _resolve_ffmpeg_path(str(exe)) == exe
    assert _resolve_ffmpeg_path(str(exe.parent)) == exe
    assert _resolve_ffmpeg_path(str(tmp_path)) == exe
    assert _resolve_ffmpeg_path(str(tmp_path / "lib")) == exe
