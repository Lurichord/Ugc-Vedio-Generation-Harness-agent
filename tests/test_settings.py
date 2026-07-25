from pathlib import Path

from ugc_harness.settings import load_shell_env


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

