from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field


_EXPORT_PATTERN = re.compile(
    r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(['"]?)(.*?)\2\s*$"""
)


def load_shell_env(path: str | Path) -> dict[str, str]:
    """Load simple KEY=value or export KEY=value lines without executing the file."""
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _EXPORT_PATTERN.match(line)
        if match:
            values[match.group(1)] = match.group(3)
    return values


class LLMSettings(BaseModel):
    api_key: str = Field(min_length=8)
    base_url: str
    model: str = "google/gemini-2.5-flash"
    timeout_seconds: float = 120.0
    max_retries: int = 2

    @classmethod
    def from_environment(
        cls,
        api_keys_file: str | Path | None = None,
        model: str | None = None,
    ) -> "LLMSettings":
        config_path = Path(api_keys_file) if api_keys_file else Path(".env")
        file_values = load_shell_env(config_path) if config_path.is_file() else {}

        def get(name: str, fallback: str | None = None) -> str | None:
            return os.getenv(name) or file_values.get(name) or fallback

        api_key = get("OPENROUTER_API_KEY") or get("OPENAI_API_KEY")
        base_url = get(
            "OPENROUTER_BASE_URL",
            get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
        )
        selected_model = model or get("UGC_LLM_MODEL", cls.model_fields["model"].default)
        if not api_key:
            raise ValueError(
                "Missing API key. Set OPENROUTER_API_KEY/OPENAI_API_KEY "
                "or pass --api-keys-file."
            )
        return cls(api_key=api_key, base_url=base_url, model=selected_model)


class AssetGenerationSettings(BaseModel):
    image_model: str = "google/gemini-3.1-flash-lite-image"
    video_model: str = "google/veo-3.1-lite"
    video_resolution: str = "720p"
    video_poll_seconds: float = 30.0
    video_timeout_seconds: float = 600.0

    @classmethod
    def from_environment(
        cls,
        api_keys_file: str | Path | None = None,
        *,
        image_model: str | None = None,
        video_model: str | None = None,
    ) -> "AssetGenerationSettings":
        config_path = Path(api_keys_file) if api_keys_file else Path(".env")
        file_values = load_shell_env(config_path) if config_path.is_file() else {}

        def get(name: str, fallback: str) -> str:
            return os.getenv(name) or file_values.get(name) or fallback

        return cls(
            image_model=image_model
            or get(
                "UGC_IMAGE_MODEL",
                cls.model_fields["image_model"].default,
            ),
            video_model=video_model
            or get(
                "UGC_VIDEO_MODEL",
                cls.model_fields["video_model"].default,
            ),
            video_resolution=get(
                "UGC_VIDEO_RESOLUTION",
                cls.model_fields["video_resolution"].default,
            ),
        )


class TTSSettings(BaseModel):
    api_key: str = Field(min_length=8)
    endpoint: str = "https://openspeech.bytedance.com/api/v1/tts"
    resource_id: str = "volc.service_type.10029"
    voice_id: str = "zh_male_qingshuangnanda_mars_bigtts"
    sample_rate: int = 24_000
    timeout_seconds: float = 90.0
    max_retries: int = 2

    @classmethod
    def from_environment(
        cls, api_keys_file: str | Path | None = None
    ) -> "TTSSettings":
        config_path = Path(api_keys_file) if api_keys_file else Path(".env")
        file_values = load_shell_env(config_path) if config_path.is_file() else {}

        def get(name: str, fallback: str | None = None) -> str | None:
            return os.getenv(name) or file_values.get(name) or fallback

        api_key = get("VOLCENGINE_TTS_API_KEY")
        if not api_key:
            raise ValueError(
                "Missing VOLCENGINE_TTS_API_KEY in environment or .env"
            )
        return cls(
            api_key=api_key,
            endpoint=get(
                "VOLCENGINE_TTS_ENDPOINT",
                cls.model_fields["endpoint"].default,
            ),
            resource_id=get(
                "VOLCENGINE_TTS_RESOURCE_ID",
                cls.model_fields["resource_id"].default,
            ),
            voice_id=get(
                "VOLCENGINE_TTS_VOICE_ID",
                cls.model_fields["voice_id"].default,
            ),
        )
