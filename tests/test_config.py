"""Tests for environment configuration loading."""

from __future__ import annotations

from datetime import UTC, time
from pathlib import Path

import pytest

from telejournal.config import (
    _parse_daily_brief_time_utc,
    _normalize_allowed_user_ids,
    _parse_allowed_user_ids,
    _parse_tag_choices,
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
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", "09:30")
    monkeypatch.setenv("TAG_CHOICES", "family,focus")
    monkeypatch.setenv("PROMPT_FOR_MOOD_IF_MISSING", "false")
    monkeypatch.setenv("BOT_MENU_ENABLED", "false")

    settings = load_settings()

    assert settings.telegram_token == "token"
    assert settings.vault_root.exists()
    assert settings.log_level == "DEBUG"
    assert settings.allowed_user_ids == {1, 2}
    assert settings.message_timestamp_window_seconds == 90
    assert settings.daily_brief_time_utc is not None
    assert settings.daily_brief_time_utc.strftime("%H:%M:%S") == "09:30:00"
    assert settings.tag_choices == ("family", "focus")
    assert settings.prompt_for_mood_if_missing is False
    assert settings.bot_menu_enabled is False


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


def test_load_settings_defaults_tag_choices_and_mood_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tag choices and mood-prompt toggle should default predictably."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.delenv("TAG_CHOICES", raising=False)
    monkeypatch.delenv("PROMPT_FOR_MOOD_IF_MISSING", raising=False)
    monkeypatch.delenv("BOT_MENU_ENABLED", raising=False)

    settings = load_settings()

    assert settings.tag_choices == (
        "family",
        "health",
        "love",
        "hobby",
        "other",
        "finance",
        "social",
    )
    assert settings.prompt_for_mood_if_missing is True
    assert settings.bot_menu_enabled is True


def test_load_settings_daily_brief_defaults_to_morning_utc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Daily brief should default to 09:00 UTC when not configured."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.delenv("DAILY_BRIEF_TIME_UTC", raising=False)

    settings = load_settings()
    assert settings.daily_brief_time_utc == time(9, 0, tzinfo=UTC)


def test_load_settings_daily_brief_explicit_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Daily brief should be disabled when explicitly set to 0."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", "0")

    settings = load_settings()
    assert settings.daily_brief_time_utc is None


def test_parse_daily_brief_time_none_and_numeric_zero() -> None:
    """Daily brief parser should support disabled values from varied sources."""
    assert _parse_daily_brief_time_utc(None) is None
    assert _parse_daily_brief_time_utc(0) is None


@pytest.mark.parametrize("value", ["25:00", "9:30", "abc", "24:00:00"])
def test_load_settings_rejects_invalid_daily_brief_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    """Invalid daily brief values should fail fast with a clear error."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", value)

    with pytest.raises(ValueError, match="DAILY_BRIEF_TIME_UTC"):
        load_settings()


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
                'allowed_user_ids: "10,20"',
                "log_level: warning",
                "message_timestamp_window_seconds: 30",
                "secure_file_permissions: false",
                "daily_brief_time_utc: '07:45'",
                "tag_choices:",
                "  - family",
                "  - focus",
                "prompt_for_mood_if_missing: false",
                "bot_menu_enabled: false",
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
    assert settings.daily_brief_time_utc is not None
    assert settings.daily_brief_time_utc.strftime("%H:%M:%S") == "07:45:00"
    assert settings.tag_choices == ("family", "focus")
    assert settings.prompt_for_mood_if_missing is False
    assert settings.bot_menu_enabled is False


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
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", "08:00")
    monkeypatch.setenv("TAG_CHOICES", "envtag")
    monkeypatch.setenv("PROMPT_FOR_MOOD_IF_MISSING", "false")
    monkeypatch.setenv("BOT_MENU_ENABLED", "false")

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: yaml-token",
                f"vault_root: {tmp_path / 'yaml-vault'}",
                'allowed_user_ids: "3,4"',
                "log_level: warning",
                "message_timestamp_window_seconds: 20",
                "daily_brief_time_utc: '10:15'",
                "tag_choices: [yaml, tags]",
                "prompt_for_mood_if_missing: true",
                "bot_menu_enabled: true",
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
            "daily_brief_time_utc": "13:45",
            "tag_choices": ["cli", "tags"],
            "prompt_for_mood_if_missing": False,
            "bot_menu_enabled": False,
        },
    )

    assert settings.telegram_token == "cli-token"
    assert settings.vault_root == (tmp_path / "yaml-vault").resolve()
    assert settings.allowed_user_ids == {3, 4}
    assert settings.log_level == "DEBUG"
    assert settings.message_timestamp_window_seconds == 99
    assert settings.daily_brief_time_utc is not None
    assert settings.daily_brief_time_utc.strftime("%H:%M:%S") == "13:45:00"
    assert settings.tag_choices == ("cli", "tags")
    assert settings.prompt_for_mood_if_missing is False
    assert settings.bot_menu_enabled is False


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
                'allowed_user_ids: "11,12"',
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


def test_parse_tag_choices_valid_values() -> None:
    """Tag-choice parser should normalize, deduplicate, and preserve order."""
    assert _parse_tag_choices("family, focus, FAMILY") == ("family", "focus")
    assert _parse_tag_choices(["health", "Health", "hobby"]) == (
        "health",
        "hobby",
    )


@pytest.mark.parametrize(
    "value",
    ["", "!!!", "bad tag", ["x", "bad tag"], 123],
)
def test_parse_tag_choices_rejects_invalid_values(value: object) -> None:
    """Invalid tag choices should fail with a clear validation error."""
    with pytest.raises(ValueError, match="TAG_CHOICES"):
        _parse_tag_choices(value)
