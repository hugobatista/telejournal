"""Tests for environment configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from telejournal.config import (
    _normalize_allowed_user_ids,
    _parse_allowed_user_ids,
    load_settings,
)


def test_parse_allowed_user_ids_empty() -> None:
    """Empty values should raise an error."""
    with pytest.raises(ValueError, match="at least one valid user ID"):
        _parse_allowed_user_ids("")
    with pytest.raises(ValueError, match="at least one valid user ID"):
        _parse_allowed_user_ids(" , , ")


def test_parse_allowed_user_ids_values() -> None:
    """CSV list should parse to integer set and ignore empty segments."""
    parsed = _parse_allowed_user_ids("123, , 456,789,")
    assert parsed == {123, 456, 789}


def test_load_settings_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing token should fail fast at startup."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("VAULT_ROOT", "/tmp/vault")

    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        load_settings()


def test_load_settings_requires_vault_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing vault root should fail fast at startup."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    with pytest.raises(ValueError, match="VAULT_ROOT"):
        load_settings()


def test_load_settings_requires_allowed_user_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing allowed user IDs should fail fast at startup."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", "/tmp/vault")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    with pytest.raises(ValueError, match="TELEGRAM_ALLOWED_USER_IDS"):
        load_settings()


def test_load_settings_builds_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Environment values should be normalized to runtime settings."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1, 2")
    monkeypatch.setenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "90")

    settings = load_settings()

    assert settings.telegram_token == "token"
    assert settings.vault_root.exists()
    assert settings.log_level == "DEBUG"
    assert settings.allowed_user_ids == {1, 2}
    assert settings.message_timestamp_window_seconds == 90


def test_load_settings_defaults_window_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Window setting should default to 60 seconds when absent."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.delenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", raising=False)

    settings = load_settings()
    assert settings.message_timestamp_window_seconds == 60


def test_load_settings_rejects_negative_window_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Negative window values should fail fast."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.setenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "-1")

    with pytest.raises(ValueError, match="MESSAGE_TIMESTAMP_WINDOW_SECONDS"):
        load_settings()


def test_load_settings_secure_permissions_default_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Secure file permissions should default to True when not specified."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.delenv("SECURE_FILE_PERMISSIONS", raising=False)

    settings = load_settings()
    assert settings.secure_file_permissions is True


def test_load_settings_secure_permissions_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Secure file permissions should parse various true/false values."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")

    # Test true values
    for true_value in ["true", "TRUE", "True", "1", "yes", "YES", "on", "ON"]:
        monkeypatch.setenv("SECURE_FILE_PERMISSIONS", true_value)
        settings = load_settings()
        assert settings.secure_file_permissions is True, f"Failed for: {true_value}"

    # Test false values
    for false_value in ["false", "FALSE", "False", "0", "no", "NO", "off", "OFF"]:
        monkeypatch.setenv("SECURE_FILE_PERMISSIONS", false_value)
        settings = load_settings()
        assert settings.secure_file_permissions is False, f"Failed for: {false_value}"


def test_load_settings_yaml_values(tmp_path: Path) -> None:
    """YAML configuration should be loaded when file path is provided."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: yaml-token",
                f"vault_root: {tmp_path / 'vault'}",
                "allowed_user_ids: \"10,20\"",
                "log_level: warning",
                "message_timestamp_window_seconds: 30",
                "secure_file_permissions: false",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=yaml_path)

    assert settings.telegram_token == "yaml-token"
    assert settings.allowed_user_ids == {10, 20}
    assert settings.log_level == "WARNING"
    assert settings.message_timestamp_window_seconds == 30
    assert settings.secure_file_permissions is False


def test_load_settings_priority_cli_over_yaml_over_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI should override YAML, and YAML should override environment values."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "env-token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "env-vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1,2")
    monkeypatch.setenv("LOG_LEVEL", "error")
    monkeypatch.setenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "10")

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: yaml-token",
                f"vault_root: {tmp_path / 'yaml-vault'}",
                "allowed_user_ids: \"3,4\"",
                "log_level: warning",
                "message_timestamp_window_seconds: 20",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(
        config_path=yaml_path,
        cli_overrides={
            "telegram_token": "cli-token",
            "log_level": "debug",
            "message_timestamp_window_seconds": 99,
        },
    )

    assert settings.telegram_token == "cli-token"
    assert settings.vault_root == (tmp_path / "yaml-vault").resolve()
    assert settings.allowed_user_ids == {3, 4}
    assert settings.log_level == "DEBUG"
    assert settings.message_timestamp_window_seconds == 99


def test_load_settings_cli_env_expansion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI values should support ${VAR} environment expansion."""
    monkeypatch.setenv("MY_TOKEN", "expanded-token")

    settings = load_settings(
        cli_overrides={
            "telegram_token": "${MY_TOKEN}",
            "vault_root": str(tmp_path / "vault"),
            "allowed_user_ids": "123",
        }
    )

    assert settings.telegram_token == "expanded-token"


def test_load_settings_yaml_env_expansion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """YAML values should support ${VAR} environment expansion."""
    monkeypatch.setenv("YAML_TOKEN", "yaml-expanded")

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: ${YAML_TOKEN}",
                f"vault_root: {tmp_path / 'vault'}",
                "allowed_user_ids: \"11,12\"",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=yaml_path)
    assert settings.telegram_token == "yaml-expanded"


def test_load_settings_yaml_missing_file(tmp_path: Path) -> None:
    """Loading with a non-existing YAML path should raise FileNotFoundError."""
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_settings(config_path=missing)


def test_normalize_allowed_user_ids_sequence() -> None:
    """Allowed IDs should support list/tuple/set inputs."""
    assert _normalize_allowed_user_ids([1, "2"]) == {1, 2}
    assert _normalize_allowed_user_ids((3, "4")) == {3, 4}


def test_normalize_allowed_user_ids_invalid_type() -> None:
    """Unsupported allowed-user-ids values should raise a clear error."""
    with pytest.raises(ValueError, match="CSV string or list"):
        _normalize_allowed_user_ids(12.3)


def test_normalize_allowed_user_ids_empty_sequence() -> None:
    """Empty list-like values should be rejected."""
    with pytest.raises(ValueError, match="at least one valid user ID"):
        _normalize_allowed_user_ids([])
