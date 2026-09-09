from __future__ import annotations

# import io  # only used by unused wav_info_from_bytes
import array
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from ...shared.video_frames import _ffmpeg_binary


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_ms: int


def inspect_wav(path: str | Path) -> WavInfo:
    with wave.open(str(path), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        return WavInfo(
            sample_rate=sample_rate,
            channels=audio.getnchannels(),
            sample_width_bytes=audio.getsampwidth(),
            frame_count=frame_count,
            duration_ms=round(frame_count / sample_rate * 1000),
        )


def concatenate_wavs(
    parts: list[tuple[Path, int, int]],
    output_path: str | Path,
) -> WavInfo:
    """Join WAV files with (pause_before_ms, pause_after_ms) silence."""
    if not parts:
        raise ValueError("at least one WAV part is required")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    reference: tuple[int, int, int] | None = None
    buffers: list[bytes] = []
    total_frames = 0
    for path, pause_before_ms, pause_after_ms in parts:
        with wave.open(str(path), "rb") as source:
            params = (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
            )
            if reference is None:
                reference = params
            elif params != reference:
                raise ValueError(
                    f"WAV format mismatch for {path}: {params} != {reference}"
                )
            channels, sample_width, sample_rate = params
            before_frames = round(sample_rate * pause_before_ms / 1000)
            after_frames = round(sample_rate * pause_after_ms / 1000)
            buffers.append(bytes(before_frames * channels * sample_width))
            buffers.append(source.readframes(source.getnframes()))
            buffers.append(bytes(after_frames * channels * sample_width))
            total_frames += before_frames + source.getnframes() + after_frames

    assert reference is not None
    channels, sample_width, sample_rate = reference
    with wave.open(str(output), "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width)
        destination.setframerate(sample_rate)
        for data in buffers:
            destination.writeframes(data)
    return WavInfo(
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=total_frames,
        duration_ms=round(total_frames / sample_rate * 1000),
    )


def fit_wav_to_windows(
    source: str | Path,
    windows: list[tuple[int, int, int]],
    output: str | Path,
) -> WavInfo:
    """Slice ``source`` by old windows and stretch each slice to target_ms.

    ``windows`` items are ``(old_start_ms, old_end_ms, target_ms)``.
    """

    src = Path(source)
    dest = Path(output)
    if not windows:
        raise ValueError("at least one duration window is required")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ugc_audio_fit_") as tmp:
        tmp_dir = Path(tmp)
        parts: list[tuple[Path, int, int]] = []
        for index, (start_ms, end_ms, target_ms) in enumerate(windows):
            sliced = tmp_dir / f"slice_{index:03d}.wav"
            fitted = tmp_dir / f"fit_{index:03d}.wav"
            _slice_wav(src, start_ms, max(end_ms, start_ms + 1), sliced)
            _set_wav_duration(sliced, max(1, int(target_ms)), fitted)
            parts.append((fitted, 0, 0))
        return concatenate_wavs(parts, dest)


def force_wav_duration(path: str | Path, target_ms: int) -> WavInfo:
    source = Path(path)
    tmp = source.with_name(source.stem + ".forced.wav")
    _set_wav_duration(source, max(1, int(target_ms)), tmp)
    tmp.replace(source)
    return inspect_wav(source)


def _slice_wav(source: Path, start_ms: int, end_ms: int, output: Path) -> None:
    info = inspect_wav(source)
    start_frame = max(0, round(info.sample_rate * start_ms / 1000))
    end_frame = max(start_frame + 1, round(info.sample_rate * end_ms / 1000))
    frame_size = info.channels * info.sample_width_bytes
    with wave.open(str(source), "rb") as audio:
        audio.setpos(min(start_frame, info.frame_count))
        frames = audio.readframes(max(0, min(end_frame, info.frame_count) - start_frame))
    if len(frames) < frame_size:
        frames = bytes(frame_size)
    _write_wav(output, info, frames)


def _set_wav_duration(source: Path, target_ms: int, output: Path) -> None:
    info = inspect_wav(source)
    target_frames = max(1, round(info.sample_rate * target_ms / 1000))
    with wave.open(str(source), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
    current_frames = len(frames) // max(1, info.channels * info.sample_width_bytes)
    if current_frames == target_frames:
        _write_wav(output, info, frames)
        return
    stretched = _stretch_frames(frames, info, target_frames)
    _write_wav(output, info, stretched)


def _stretch_frames(frames: bytes, info: WavInfo, target_frames: int) -> bytes:
    frame_size = info.channels * info.sample_width_bytes
    current_frames = len(frames) // frame_size if frame_size else 0
    if current_frames <= 0:
        return bytes(target_frames * frame_size)
    ffmpeg_stretched = _atempo_frames(frames, info, current_frames, target_frames)
    if ffmpeg_stretched is not None:
        return _force_frame_count(ffmpeg_stretched, frame_size, target_frames)
    return _interpolate_frames(frames, info, current_frames, target_frames)


def _atempo_frames(
    frames: bytes,
    info: WavInfo,
    current_frames: int,
    target_frames: int,
) -> bytes | None:
    speed = current_frames / target_frames
    if abs(speed - 1) < 0.01:
        return _force_frame_count(frames, info.channels * info.sample_width_bytes, target_frames)
    try:
        ffmpeg = _ffmpeg_binary()
    except FileNotFoundError:
        return None
    with tempfile.TemporaryDirectory(prefix="ugc_atempo_") as tmp:
        tmp_dir = Path(tmp)
        source = tmp_dir / "in.wav"
        dest = tmp_dir / "out.wav"
        _write_wav(source, info, frames)
        completed = subprocess.run(
            [
                str(ffmpeg),
                "-y",
                "-i",
                str(source),
                "-filter:a",
                _atempo_filter(speed),
                "-ac",
                str(info.channels),
                "-ar",
                str(info.sample_rate),
                str(dest),
            ],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not dest.is_file():
            return None
        with wave.open(str(dest), "rb") as audio:
            return audio.readframes(audio.getnframes())


def _atempo_filter(speed: float) -> str:
    parts: list[str] = []
    remaining = max(0.05, min(100.0, speed))
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def _interpolate_frames(
    frames: bytes,
    info: WavInfo,
    current_frames: int,
    target_frames: int,
) -> bytes:
    if info.sample_width_bytes != 2:
        return _force_frame_count(
            frames, info.channels * info.sample_width_bytes, target_frames
        )
    samples = array.array("h")
    samples.frombytes(frames)
    channel_count = max(1, info.channels)
    old_count = len(samples) // channel_count
    if old_count <= 0:
        return bytes(target_frames * channel_count * 2)
    resized = array.array("h")
    last = max(1, old_count - 1)
    for index in range(target_frames):
        src = index * last / max(1, target_frames - 1) if target_frames > 1 else 0
        left = int(src)
        frac = src - left
        right = min(left + 1, old_count - 1)
        for channel in range(channel_count):
            a = samples[left * channel_count + channel]
            b = samples[right * channel_count + channel]
            resized.append(int(a * (1 - frac) + b * frac))
    return resized.tobytes()


def _force_frame_count(frames: bytes, frame_size: int, target_frames: int) -> bytes:
    needed = target_frames * frame_size
    if len(frames) >= needed:
        return frames[:needed]
    return frames + bytes(needed - len(frames))


def _write_wav(path: Path, info: WavInfo, frames: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(info.channels)
        audio.setsampwidth(info.sample_width_bytes)
        audio.setframerate(info.sample_rate)
        audio.writeframes(frames)


# Unused after VoiceAgent was folded into GenericAgent / VoiceCapabilities.
# def wav_info_from_bytes(data: bytes) -> WavInfo:
#     with wave.open(io.BytesIO(data), "rb") as audio:
#         frame_count = audio.getnframes()
#         sample_rate = audio.getframerate()
#         return WavInfo(
#             sample_rate=sample_rate,
#             channels=audio.getnchannels(),
#             sample_width_bytes=audio.getsampwidth(),
#             frame_count=frame_count,
#             duration_ms=round(frame_count / sample_rate * 1000),
#         )
