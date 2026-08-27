from __future__ import annotations

# import io  # only used by unused wav_info_from_bytes
import wave
from dataclasses import dataclass
from pathlib import Path


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
