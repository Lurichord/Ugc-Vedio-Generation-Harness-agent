from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from ...shared.settings import TTSSettings


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
    def __init__(self, settings: TTSSettings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.timeout_seconds)

    def close(self) -> None:
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
                "X-Api-Key": self.settings.api_key,
                "X-Api-Resource-Id": self.settings.resource_id,
                "X-Api-Request-Id": request_id,
            }
            payload = {
                "app": {
                    "appid": "ugc-harness",
                    "token": "unused-with-x-api-key",
                    "cluster": "volcano_tts",
                },
                "user": {"uid": "ugc-video-harness"},
                "audio": {
                    "voice_type": self.settings.voice_id,
                    "encoding": "wav",
                    "rate": self.settings.sample_rate,
                    "speed_ratio": speed_ratio,
                    "loudness_ratio": 1.0,
                },
                "request": {
                    "reqid": request_id,
                    "text": text,
                    "operation": "query",
                    "with_timestamp": 1,
                    "extra_param": json.dumps(
                        {"disable_markdown_filter": True},
                        ensure_ascii=False,
                    ),
                },
            }
            response = self.client.post(
                self.settings.endpoint,
                headers=headers,
                json=payload,
            )
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"TTS returned non-JSON response: HTTP {response.status_code}"
                ) from exc

            code = data.get("code")
            if response.status_code == 200 and code == 3000 and data.get("data"):
                audio_bytes = base64.b64decode(data["data"], validate=True)
                if not audio_bytes.startswith(b"RIFF"):
                    raise RuntimeError("TTS response is not a valid WAV file")
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(audio_bytes)
                addition = data.get("addition") or {}
                duration_ms = int(float(addition.get("duration") or 0))
                return SynthesisResult(
                    request_id=request_id,
                    log_id=response.headers.get("X-Tt-Logid"),
                    duration_ms=duration_ms,
                    audio_bytes=audio_bytes,
                    words=_parse_native_words(addition.get("frontend")),
                )

            last_error = (
                f"HTTP {response.status_code}, code={code}, "
                f"message={data.get('message')}"
            )
            if code not in {3003, 3005, 3030, 3032, 3040}:
                break
            if attempt < self.settings.max_retries:
                time.sleep(min(2**attempt, 4))
        raise RuntimeError(f"TTS synthesis failed: {last_error}")


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
        start_ms = int(item.get("start_time") or 0)
        end_ms = int(item.get("end_time") or 0)
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
