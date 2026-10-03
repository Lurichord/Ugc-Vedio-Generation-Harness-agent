from __future__ import annotations

import base64
import io
import json
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx

from ...shared.settings import TTSSettings

_DONE_CODE = 20000000
_RETRY_CODES = {55000000}
_SAMPLE_WIDTH = 2


@dataclass(frozen=True)
class NativeWord:
    word: str
    start_ms: int
    end_ms: int
    confidence: float | None


@dataclass(frozen=True)
class SynthesisResult:
    request_id: str
    log_id: str | None
    duration_ms: int
    audio_bytes: bytes
    words: list[NativeWord]


class VolcengineTTS:
    def __init__(
        self,
        settings: TTSSettings,
        client: httpx.Client | None = None,
    ):
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=settings.timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "VolcengineTTS":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def synthesize(
        self,
        *,
        text: str,
        speed_ratio: float,
        output_path: str | Path,
        delivery_instruction: str | None = None,
        energy: str | None = None,
        pitch: int | None = None,
        loudness_rate: int | None = None,
        model: str | None = None,
    ) -> SynthesisResult:
        if not text.strip():
            raise ValueError("TTS text cannot be empty")
        if len(text.encode("utf-8")) > 1024:
            raise ValueError("TTS segment exceeds Volcengine's 1024-byte limit")

        last_error = ""
        for attempt in range(self.settings.max_retries + 1):
            request_id = str(uuid.uuid4())
            headers = {
                "Content-Type": "application/json",
                "Connection": "keep-alive",
                "X-Api-Key": self.settings.api_key,
                "X-Api-Resource-Id": self.settings.resource_id,
                "X-Api-Request-Id": request_id,
            }
            audio_params: dict[str, object] = {
                "format": "pcm",
                "sample_rate": self.settings.sample_rate,
                "speech_rate": _speech_rate(speed_ratio),
                "enable_subtitle": True,
            }
            loudness = (
                loudness_rate
                if loudness_rate is not None
                else _loudness_for_energy(energy)
            )
            if loudness is not None:
                audio_params["loudness_rate"] = max(-50, min(100, int(loudness)))
            additions: dict[str, object] = {"disable_markdown_filter": True}
            instruction = (delivery_instruction or "").strip()
            if instruction:
                # 官方 2.0：context_texts 只取第一段，写成对模型说的语音指令。
                additions["context_texts"] = [instruction]
            if pitch is not None:
                additions["post_process"] = {"pitch": max(-12, min(12, int(pitch)))}
            req_params: dict[str, object] = {
                "text": text,
                "speaker": self.settings.voice_id,
                "audio_params": audio_params,
                "additions": json.dumps(additions, ensure_ascii=False),
            }
            if model:
                req_params["model"] = model
            payload = {
                "user": {"uid": "ugc-video-harness"},
                "req_params": req_params,
            }
            response = self.client.post(
                self.settings.endpoint,
                headers=headers,
                json=payload,
            )
            frames = _parse_v3_frames(response.text)
            audio_pcm, words, error = _collect_v3_result(frames)
            if response.status_code == 200 and audio_pcm and error is None:
                wav_bytes = _pcm_to_wav(audio_pcm, self.settings.sample_rate)
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(wav_bytes)
                duration_ms = round(
                    len(audio_pcm)
                    / (self.settings.sample_rate * _SAMPLE_WIDTH)
                    * 1000
                )
                return SynthesisResult(
                    request_id=request_id,
                    log_id=response.headers.get("X-Tt-Logid"),
                    duration_ms=duration_ms,
                    audio_bytes=wav_bytes,
                    words=words,
                )

            last_error = error or (
                f"HTTP {response.status_code}, code={_first_error_code(frames)}, "
                f"message={_first_error_message(frames) or response.text[:200]}"
            )
            code = _first_error_code(frames)
            if code not in _RETRY_CODES and "concurrency" not in last_error.lower():
                break
            if attempt < self.settings.max_retries:
                time.sleep(min(2**attempt, 4))
        raise RuntimeError(f"TTS synthesis failed: {last_error}")


def _speech_rate(speed_ratio: float) -> int:
    return max(-50, min(100, round((speed_ratio - 1.0) * 100)))


def _loudness_for_energy(energy: str | None) -> int | None:
    if energy == "low":
        return 20
    if energy == "medium":
        return 50
    if energy == "high":
        return 85
    return None


def _parse_v3_frames(text: str) -> list[dict]:
    frames: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("event:"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            frames.append(payload)
    if frames:
        return frames
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            payload, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        if isinstance(payload, dict):
            frames.append(payload)
        index = end
    return frames


def _frame_status(frame: dict) -> tuple[object, str]:
    header = frame.get("header")
    if isinstance(header, dict):
        code = header.get("code", frame.get("code"))
        message = header.get("message") or frame.get("message") or ""
        return code, str(message)
    return frame.get("code"), str(frame.get("message") or "")


def _collect_v3_result(
    frames: list[dict],
) -> tuple[bytes, list[NativeWord], str | None]:
    pcm = bytearray()
    words: list[NativeWord] = []
    finished = False
    for frame in frames:
        code, message = _frame_status(frame)
        if code not in {0, _DONE_CODE}:
            return b"", [], f"code={code}, message={message}"
        data = frame.get("data")
        if isinstance(data, str) and data:
            pcm.extend(base64.b64decode(data, validate=True))
        sentence = frame.get("sentence")
        if isinstance(sentence, dict):
            words.extend(_parse_native_words(sentence))
        if code == _DONE_CODE:
            finished = True
    if not pcm:
        return b"", words, "no audio frames"
    if not finished:
        return bytes(pcm), words, "stream ended before SessionFinish"
    return bytes(pcm), words, None


def _first_error_code(frames: list[dict]) -> object:
    for frame in frames:
        code, _message = _frame_status(frame)
        if code not in {0, _DONE_CODE, None}:
            return code
    return None


def _first_error_message(frames: list[dict]) -> str:
    for frame in frames:
        code, message = _frame_status(frame)
        if code not in {0, _DONE_CODE, None}:
            return message
    return ""


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(_SAMPLE_WIDTH)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm)
    return buffer.getvalue()


def _parse_native_words(frontend: object) -> list[NativeWord]:
    if not frontend:
        return []
    if isinstance(frontend, str):
        try:
            value = json.loads(frontend)
        except json.JSONDecodeError:
            return []
    elif isinstance(frontend, dict):
        value = frontend
    else:
        return []
    words: list[NativeWord] = []
    for item in value.get("words", []):
        word = str(item.get("word") or "").strip()
        start_ms = _seconds_to_ms(item.get("startTime"))
        end_ms = _seconds_to_ms(item.get("endTime"))
        if not word or end_ms <= start_ms:
            continue
        confidence = item.get("confidence")
        words.append(
            NativeWord(
                word=word,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=float(confidence) if confidence is not None else None,
            )
        )
    return words


def _seconds_to_ms(value: object) -> int:
    if value is None or value == "":
        return 0
    return int(round(float(value) * 1000))
