from pathlib import Path

from ugc_harness.shared.settings import (
    AssetGenerationSettings,
    EmbeddingSettings,
    LLMSettings,
    TTSSettings,
    load_shell_env,
)


def test_load_shell_env_without_executing(tmp_path: Path) -> None:
    config = tmp_path / "keys"
    config.write_text(
        '# comment\nexport VOLCENGINE_ARK_API_KEY="secret-value"\n'
        "VOLCENGINE_ARK_BASE_URL='https://example.test/v1'\n"
        "this is not executable\n",
        encoding="utf-8",
    )

    values = load_shell_env(config)

    assert values == {
        "VOLCENGINE_ARK_API_KEY": "secret-value",
        "VOLCENGINE_ARK_BASE_URL": "https://example.test/v1",
    }


def test_tts_settings_load_new_volcengine_key(tmp_path: Path) -> None:
    config = tmp_path / ".env"
    config.write_text(
        'VOLCENGINE_TTS_API_KEY="00000000-0000-0000-0000-000000000000"\n'
        'VOLCENGINE_TTS_VOICE_ID="voice-test"\n',
        encoding="utf-8",
    )

    settings = TTSSettings.from_environment(config)

    assert settings.voice_id == "voice-test"
    assert settings.resource_id == "seed-tts-2.0"
    assert settings.endpoint.endswith("/api/v3/tts/unidirectional")


def test_tts_defaults_are_seed_tts_2_voices() -> None:
    assert TTSSettings.model_fields["voice_id"].default.endswith("_uranus_bigtts")
    assert TTSSettings.model_fields["female_voice_id"].default.endswith(
        "_uranus_bigtts"
    )
    assert TTSSettings.model_fields["endpoint"].default.endswith(
        "/api/v3/tts/unidirectional"
    )


def test_video_generation_uses_seedance_specific_configuration(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".env"
    config.write_text(
        'UGC_VIDEO_MODEL="doubao-seedance-2-0-260128"\n'
        'UGC_VIDEO_API_KEY="seedance-test-key"\n'
        'UGC_VIDEO_BASE_URL="https://seedance.example/v1"\n',
        encoding="utf-8",
    )

    settings = AssetGenerationSettings.from_environment(config)

    assert settings.video_model == "doubao-seedance-2-0-260128"
    assert settings.video_api_key == "seedance-test-key"
    assert settings.video_base_url == "https://seedance.example/v1"


def test_volcengine_ark_key_drives_text_and_image_models(tmp_path: Path) -> None:
    config = tmp_path / ".env"
    config.write_text(
        'VOLCENGINE_ARK_API_KEY="ark-test-key-123"\n'
        'VOLCENGINE_ARK_BASE_URL="https://ark.example/api/v3"\n',
        encoding="utf-8",
    )

    llm = LLMSettings.from_environment(config)
    media = AssetGenerationSettings.from_environment(config)

    assert llm.model == "doubao-seed-2-0-lite-260215"
    assert llm.base_url == "https://ark.example/api/v3"
    assert media.image_model == "doubao-seedream-5-0-260128"
    assert media.image_api_key == "ark-test-key-123"


def test_embedding_settings_default_model_is_vision() -> None:
    assert EmbeddingSettings.model_fields["model"].default == (
        "doubao-embedding-vision-251215"
    )


def test_embedding_settings_read_model_from_env(tmp_path: Path) -> None:
    config = tmp_path / ".env"
    config.write_text(
        'VOLCENGINE_ARK_API_KEY="ark-test-key-123"\n'
        'UGC_EMBEDDING_MODEL="doubao-embedding-text-240515"\n',
        encoding="utf-8",
    )

    settings = EmbeddingSettings.from_environment(config)

    assert settings.model == "doubao-embedding-text-240515"
    assert settings.api_key == "ark-test-key-123"


def test_embedding_settings_missing_key_returns_none(tmp_path: Path) -> None:
    config = tmp_path / ".env"
    config.write_text("UGC_LLM_MODEL=demo\n", encoding="utf-8")
    assert EmbeddingSettings.try_from_environment(config) is None
