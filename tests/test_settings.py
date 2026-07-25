from pathlib import Path

from ugc_harness.settings import TTSSettings, load_shell_env


def test_load_shell_env_without_executing(tmp_path: Path) -> None:
    config = tmp_path / "keys"
    config.write_text(
        '# comment\nexport OPENROUTER_API_KEY="secret-value"\n'
        "OPENROUTER_BASE_URL='https://example.test/v1'\n"
        "this is not executable\n",
        encoding="utf-8",
    )

    values = load_shell_env(config)

    assert values == {
        "OPENROUTER_API_KEY": "secret-value",
        "OPENROUTER_BASE_URL": "https://example.test/v1",
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
    assert settings.resource_id == "volc.service_type.10029"
