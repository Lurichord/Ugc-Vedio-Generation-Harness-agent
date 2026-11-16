from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SUCCESS_DONE = 20000000


def resolve_voice_id(voice_id: str | None) -> str:
    resolved = (voice_id or "").strip() or os.environ.get("VOLCENGINE_TTS_VOICE_ID", "")
    if not resolved:
        raise ValueError("voice_id is missing and VOLCENGINE_TTS_VOICE_ID is not set")
    return resolved


def parse_tts_stream(lines: Iterable[str | bytes]) -> tuple[bytes, list[dict[str, Any]]]:
    audio = bytearray()
    words: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    finished = False
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        code = int(payload.get("code") or 0)
        if code == SUCCESS_DONE:
            finished = True
            break
        if code > 0:
            raise RuntimeError(payload.get("message") or f"tts failed with code {code}")
        data = payload.get("data")
        if data:
            audio.extend(base64.b64decode(data))
        sentence = payload.get("sentence") or {}
        for item in sentence.get("words") or []:
            key = (item.get("word"), item.get("startTime"), item.get("endTime"))
            if key in seen:
                continue
            seen.add(key)
            words.append(
                {
                    "word": item["word"],
                    "start": float(item["startTime"]),
                    "end": float(item["endTime"]),
                }
            )
    if not audio:
        raise RuntimeError("tts returned no audio" if finished else "tts stream ended without audio")
    return bytes(audio), words


def synthesize_speech(
    text: str,
    *,
    voice_id: str,
    speed: int = 0,
    pitch: int = 0,
    volume: int = 0,
) -> tuple[bytes, list[dict[str, Any]]]:
    api_key = os.environ.get("VOLCENGINE_TTS_API_KEY", "")
    if not api_key:
        raise ValueError("VOLCENGINE_TTS_API_KEY is missing")
    endpoint = os.environ.get("VOLCENGINE_ARK_BASE_URL", "").strip()
    if not endpoint:
        raise ValueError("VOLCENGINE_ARK_BASE_URL is missing")
    resource_id = os.environ.get("VOLCENGINE_TTS_RESOURCE_ID", "").strip()
    if not resource_id:
        raise ValueError("VOLCENGINE_TTS_RESOURCE_ID is missing")
    body = json.dumps(
        {
            "req_params": {
                "text": text,
                "speaker": voice_id,
                "additions": json.dumps({"post_process": {"pitch": pitch}}),
                "audio_params": {
                    "format": "mp3",
                    "sample_rate": 24000,
                    "speech_rate": speed,
                    "loudness_rate": volume,
                    "enable_subtitle": True,
                },
            }
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return parse_tts_stream(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"tts http {exc.code}: {detail}") from exc


def write_speech_files(
    directory: Path,
    text: str,
    audio: bytes,
    words: list[dict[str, Any]],
) -> tuple[Path, Path, float]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex[:12]
    audio_path = directory / f"{stem}.mp3"
    timestamp_path = directory / f"{stem}.timestamp.json"
    audio_path.write_bytes(audio)
    timestamp_path.write_text(
        json.dumps({"text": text, "words": words}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    duration = max((word["end"] for word in words), default=0.0)
    return audio_path, timestamp_path, duration
