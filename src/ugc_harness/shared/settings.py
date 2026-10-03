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
    model: str = "doubao-seed-2-0-lite-260215"
    timeout_seconds: float = 300.0
    max_retries: int = 2
    # 火山引擎默认输出上限偏小，深度思考 token 也计入其中，
    # editorial/timeline 这类大 JSON 需要显式调大。
    max_output_tokens: int = 16_384

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

        api_key = (
            get("VOLCENGINE_ARK_API_KEY")
            or get("ARK_API_KEY")
            or get("UGC_VIDEO_API_KEY")
        )
        base_url = get(
            "VOLCENGINE_ARK_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/v3",
        )
        selected_model = model or get("UGC_LLM_MODEL", cls.model_fields["model"].default)
        if not api_key:
            raise ValueError(
                "Missing API key. Set VOLCENGINE_ARK_API_KEY/ARK_API_KEY "
                "or pass --api-keys-file."
            )
        return cls(api_key=api_key, base_url=base_url, model=selected_model)


class EmbeddingSettings(BaseModel):
    api_key: str = Field(min_length=8)
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model: str = "doubao-embedding-vision-251215"
    timeout_seconds: float = 60.0
    min_score: float = 0.35
    collection_min_score: float = 0.4
    top_k: int = 8
    candidate_k: int = 12

    @classmethod
    def try_from_environment(
        cls,
        api_keys_file: str | Path | None = None,
    ) -> "EmbeddingSettings | None":
        config_path = Path(api_keys_file) if api_keys_file else Path(".env")
        file_values = load_shell_env(config_path) if config_path.is_file() else {}

        def get(name: str, fallback: str | None = None) -> str | None:
            return os.getenv(name) or file_values.get(name) or fallback

        api_key = (
            get("VOLCENGINE_ARK_API_KEY")
            or get("ARK_API_KEY")
            or get("UGC_VIDEO_API_KEY")
        )
        if not api_key:
            return None
        min_score = get("UGC_KNOWLEDGE_MIN_SCORE")
        collection_min_score = get("UGC_KNOWLEDGE_COLLECTION_MIN_SCORE")
        top_k = get("UGC_KNOWLEDGE_TOP_K")
        candidate_k = get("UGC_KNOWLEDGE_CANDIDATE_K")
        return cls(
            api_key=api_key,
            base_url=get(
                "VOLCENGINE_ARK_BASE_URL",
                cls.model_fields["base_url"].default,
            )
            or cls.model_fields["base_url"].default,
            model=get("UGC_EMBEDDING_MODEL", cls.model_fields["model"].default)
            or cls.model_fields["model"].default,
            min_score=float(min_score) if min_score else cls.model_fields["min_score"].default,
            collection_min_score=(
                float(collection_min_score)
                if collection_min_score
                else cls.model_fields["collection_min_score"].default
            ),
            top_k=int(top_k) if top_k else cls.model_fields["top_k"].default,
            candidate_k=(
                int(candidate_k)
                if candidate_k
                else cls.model_fields["candidate_k"].default
            ),
        )

    @classmethod
    def from_environment(
        cls,
        api_keys_file: str | Path | None = None,
    ) -> "EmbeddingSettings":
        settings = cls.try_from_environment(api_keys_file)
        if settings is None:
            raise ValueError(
                "Missing API key. Set VOLCENGINE_ARK_API_KEY/ARK_API_KEY "
                "to use knowledge embeddings."
            )
        return settings


class AssetGenerationSettings(BaseModel):
    image_model: str = "doubao-seedream-5-0-260128"
    image_api_key: str | None = None
    image_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    video_model: str = "doubao-seedance-2-0-260128"
    video_api_key: str | None = None
    video_base_url: str | None = None
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

        def get_optional(name: str) -> str | None:
            return os.getenv(name) or file_values.get(name) or None

        return cls(
            image_model=image_model
            or get(
                "UGC_IMAGE_MODEL",
                cls.model_fields["image_model"].default,
            ),
            image_api_key=(
                get_optional("UGC_IMAGE_API_KEY")
                or get_optional("VOLCENGINE_ARK_API_KEY")
                or get_optional("ARK_API_KEY")
                or get_optional("UGC_VIDEO_API_KEY")
            ),
            image_base_url=get(
                "UGC_IMAGE_BASE_URL",
                cls.model_fields["image_base_url"].default,
            ),
            video_model=video_model
            or get(
                "UGC_VIDEO_MODEL",
                cls.model_fields["video_model"].default,
            ),
            video_api_key=get_optional("UGC_VIDEO_API_KEY"),
            video_base_url=get_optional("UGC_VIDEO_BASE_URL"),
            video_resolution=get(
                "UGC_VIDEO_RESOLUTION",
                cls.model_fields["video_resolution"].default,
            ),
        )


class TTSSettings(BaseModel):
    api_key: str = Field(min_length=8)
    endpoint: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    resource_id: str = "seed-tts-2.0"
    voice_id: str = "zh_male_qingshuangnanda_uranus_bigtts"
    male_voice_id: str = "zh_male_qingshuangnanda_uranus_bigtts"
    female_voice_id: str = "zh_female_shuangkuaisisi_uranus_bigtts"
    neutral_voice_id: str = "zh_male_qingshuangnanda_uranus_bigtts"
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
        settings = cls(
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
            male_voice_id=get(
                "VOLCENGINE_TTS_MALE_VOICE_ID",
                cls.model_fields["male_voice_id"].default,
            ),
            female_voice_id=get(
                "VOLCENGINE_TTS_FEMALE_VOICE_ID",
                cls.model_fields["female_voice_id"].default,
            ),
            neutral_voice_id=get(
                "VOLCENGINE_TTS_NEUTRAL_VOICE_ID",
                cls.model_fields["neutral_voice_id"].default,
            ),
        )
        return settings

    def voice_for_gender(self, gender: str) -> str:
        return {
            "male": self.male_voice_id,
            "female": self.female_voice_id,
            "neutral": self.neutral_voice_id,
        }.get(gender, self.voice_id)
