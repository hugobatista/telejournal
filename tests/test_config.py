"""Tests for application configuration loading and validation."""

from __future__ import annotations

from datetime import UTC, time
from pathlib import Path

import pytest

from telejournal.config import (
    STORAGE_PROVIDER_GITHUB,
    STORAGE_PROVIDER_GOOGLEDRIVE,
    STORAGE_PROVIDER_OBSIDIAN,
    STORAGE_PROVIDER_ONEDRIVE,
    _merge_configs,
    _normalize_allowed_user_ids,
    _parse_allowed_user_ids,
    _parse_daily_brief_time_utc,
    _parse_tag_choices,
    _resolve_config_path,
    load_settings,
)


def _set_common_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")


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
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", "/tmp/vault")

    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        load_settings()


def test_load_settings_requires_allowed_user_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing allowed user IDs should fail fast at startup."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", "/tmp/vault")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    with pytest.raises(ValueError, match="TELEGRAM_ALLOWED_USER_IDS"):
        load_settings()


def test_load_settings_obsidian_provider_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Obsidian provider should load from hierarchical storage env values."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_SECURE_FILE_PERMISSIONS", "false")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "90")
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", "09:30")
    monkeypatch.setenv("TAG_CHOICES", "family,focus")
    monkeypatch.setenv("PROMPT_FOR_MOOD_IF_MISSING", "false")
    monkeypatch.setenv("BOT_MENU_ENABLED", "false")

    settings = load_settings()

    assert settings.storage_provider == STORAGE_PROVIDER_OBSIDIAN
    assert settings.vault_root.exists()
    assert settings.secure_file_permissions is False
    assert settings.log_level == "DEBUG"
    assert settings.allowed_user_ids == {123}
    assert settings.message_timestamp_window_seconds == 90
    assert settings.daily_brief_time_utc is not None
    assert settings.daily_brief_time_utc.strftime("%H:%M:%S") == "09:30:00"
    assert settings.tag_choices == ("family", "focus")
    assert settings.prompt_for_mood_if_missing is False
    assert settings.bot_menu_enabled is False


def test_load_settings_obsidian_requires_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Obsidian provider should require storage.obsidian_vault.root."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.delenv("STORAGE_OBSIDIAN_VAULT_ROOT", raising=False)

    with pytest.raises(ValueError, match="obsidian_vault.root"):
        load_settings()


def test_load_settings_github_provider_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub provider should parse owner/repo/token and optional values."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "github_repo")
    monkeypatch.setenv("STORAGE_GITHUB_OWNER", "acme")
    monkeypatch.setenv("STORAGE_GITHUB_REPO", "journal")
    monkeypatch.setenv("STORAGE_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("STORAGE_GITHUB_BRANCH", "dev")
    monkeypatch.setenv("STORAGE_GITHUB_PATH_PREFIX", "/notes/")
    monkeypatch.setenv("STORAGE_GITHUB_API_BASE_URL", "https://api.github.com/")
    monkeypatch.setenv("STORAGE_GITHUB_BATCH_WINDOW_SECONDS", "120")

    settings = load_settings()
    assert settings.storage_provider == STORAGE_PROVIDER_GITHUB
    assert settings.github_owner == "acme"
    assert settings.github_repo == "journal"
    assert settings.github_token == "gh-token"
    assert settings.github_branch == "dev"
    assert settings.github_path_prefix == "notes"
    assert settings.github_api_base_url == "https://api.github.com/"
    assert settings.github_batch_window_seconds == 120


def test_load_settings_github_rejects_invalid_batch_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub provider should reject batch windows below one second."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "github_repo")
    monkeypatch.setenv("STORAGE_GITHUB_OWNER", "acme")
    monkeypatch.setenv("STORAGE_GITHUB_REPO", "journal")
    monkeypatch.setenv("STORAGE_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("STORAGE_GITHUB_BATCH_WINDOW_SECONDS", "0")

    with pytest.raises(ValueError, match="batch_window_seconds"):
        load_settings()


@pytest.mark.parametrize(
    "key, value, match",
    [
        ("STORAGE_GITHUB_OWNER", "", "github_repo.owner"),
        ("STORAGE_GITHUB_REPO", "", "github_repo.repo"),
        ("STORAGE_GITHUB_TOKEN", "", "github_repo.token"),
    ],
)
def test_load_settings_github_requires_fields(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
    match: str,
) -> None:
    """GitHub provider should reject missing required settings."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "github_repo")
    monkeypatch.setenv("STORAGE_GITHUB_OWNER", "acme")
    monkeypatch.setenv("STORAGE_GITHUB_REPO", "journal")
    monkeypatch.setenv("STORAGE_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=match):
        load_settings()


def test_load_settings_rejects_invalid_storage_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported storage providers should fail fast with clear message."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="storage.provider"):
        load_settings()


def test_load_settings_onedrive_provider_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OneDrive provider should parse required and optional values."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_ONEDRIVE_TENANT_ID", "common")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "Apps/telejournal")
    monkeypatch.setenv(
        "STORAGE_ONEDRIVE_API_BASE_URL",
        "https://graph.microsoft.com/v1.0",
    )
    monkeypatch.setenv("STORAGE_ONEDRIVE_BATCH_WINDOW_SECONDS", "90")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ACCESS_TOKEN", "access")
    monkeypatch.setenv("STORAGE_ONEDRIVE_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv(
        "STORAGE_ONEDRIVE_TOKEN_EXPIRES_AT_UTC",
        "2026-03-28T10:00:00Z",
    )

    settings = load_settings()
    assert settings.storage_provider == STORAGE_PROVIDER_ONEDRIVE
    assert settings.onedrive_client_id == "client-id"
    assert settings.onedrive_client_secret == "client-secret"
    assert settings.onedrive_tenant_id == "common"
    assert settings.onedrive_root_path == "Apps/telejournal"
    assert settings.onedrive_batch_window_seconds == 90
    assert settings.onedrive_access_token == "access"
    assert settings.onedrive_refresh_token == "refresh"


def test_load_settings_onedrive_requires_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OneDrive provider should require storage.onedrive.client_id."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "Apps/telejournal")
    monkeypatch.delenv("STORAGE_ONEDRIVE_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="storage.onedrive.client_id"):
        load_settings()


def test_load_settings_onedrive_rejects_invalid_batch_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OneDrive provider should reject batch windows below one second."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "Apps/telejournal")
    monkeypatch.setenv("STORAGE_ONEDRIVE_BATCH_WINDOW_SECONDS", "0")

    with pytest.raises(ValueError, match="storage.onedrive.batch_window_seconds"):
        load_settings()


def test_load_settings_onedrive_rejects_invalid_token_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OneDrive token expiry should use strict UTC timestamp format."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "Apps/telejournal")
    monkeypatch.setenv(
        "STORAGE_ONEDRIVE_TOKEN_EXPIRES_AT_UTC",
        "2026/03/28 10:00",
    )

    with pytest.raises(ValueError, match="token_expires_at_utc"):
        load_settings()


def test_load_settings_onedrive_tenant_falls_back_to_common(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OneDrive tenant should fall back to common when explicitly blank."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: token",
                "allowed_user_ids: [1]",
                "storage:",
                "  provider: onedrive",
                "  onedrive:",
                "    tenant_id: ''",
                "    client_id: client-id",
                "    client_secret: client-secret",
                "    root_path: Apps/telejournal",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)

    settings = load_settings(config_path=yaml_path)
    assert settings.onedrive_tenant_id == "common"


def test_load_settings_onedrive_rejects_empty_root_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OneDrive provider should reject empty normalized root paths."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "/")

    with pytest.raises(ValueError, match="storage.onedrive.root_path"):
        load_settings()


def test_load_settings_onedrive_requires_client_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OneDrive provider should require storage.onedrive.client_secret."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "Apps/telejournal")
    monkeypatch.delenv("STORAGE_ONEDRIVE_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="storage.onedrive.client_secret"):
        load_settings()


def test_load_settings_google_drive_provider_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google Drive provider should parse required and optional values."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "google_drive")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_FOLDER_ID", "folder-id")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_BATCH_WINDOW_SECONDS", "75")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_ACCESS_TOKEN", "access")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv(
        "STORAGE_GOOGLE_DRIVE_TOKEN_EXPIRES_AT_UTC",
        "2026-03-28T10:00:00Z",
    )

    settings = load_settings()
    assert settings.storage_provider == STORAGE_PROVIDER_GOOGLEDRIVE
    assert settings.google_drive_client_id == "client-id"
    assert settings.google_drive_client_secret == "client-secret"
    assert settings.google_drive_folder_id == "folder-id"
    assert settings.google_drive_batch_window_seconds == 75
    assert settings.google_drive_access_token == "access"
    assert settings.google_drive_refresh_token == "refresh"


def test_load_settings_google_drive_provider_with_missing_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Provider selection should not crash when storage.google_drive node is absent."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: token",
                "allowed_user_ids: [1]",
                "storage:",
                "  provider: google_drive",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="storage.google_drive.client_id"):
        load_settings(config_path=yaml_path)


def test_load_settings_google_drive_requires_client_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google Drive provider should require client secret configuration."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "google_drive")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.delenv("STORAGE_GOOGLE_DRIVE_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="storage.google_drive.client_secret"):
        load_settings()


def test_load_settings_google_drive_rejects_invalid_batch_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google Drive provider should reject batch windows below one second."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "google_drive")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_BATCH_WINDOW_SECONDS", "0")

    with pytest.raises(
        ValueError,
        match="storage.google_drive.batch_window_seconds",
    ):
        load_settings()


def test_load_settings_google_drive_rejects_invalid_token_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google Drive token expiry should use strict UTC timestamp format."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "google_drive")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_GOOGLE_DRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "STORAGE_GOOGLE_DRIVE_TOKEN_EXPIRES_AT_UTC",
        "2026/03/28 10:00",
    )

    with pytest.raises(ValueError, match="storage.google_drive.token_expires_at_utc"):
        load_settings()


def test_load_settings_defaults_and_yaml_cli_priority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI should override YAML, and YAML should override env values."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", str(tmp_path / "env-vault"))
    monkeypatch.setenv("LOG_LEVEL", "error")
    monkeypatch.setenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "10")

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: yaml-token",
                "allowed_user_ids: [3, 4]",
                "storage:",
                "  provider: obsidian_vault",
                "  obsidian_vault:",
                f"    root: {tmp_path / 'yaml-vault'}",
                "    secure_file_permissions: true",
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
    assert settings.daily_brief_time_utc == time(9, 0, tzinfo=UTC)


def test_load_settings_cli_and_yaml_env_expansion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both YAML and CLI values should support environment expansion."""
    monkeypatch.setenv("MY_TOKEN", "expanded-token")
    monkeypatch.setenv("MY_ROOT", str(tmp_path / "vault"))

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "telegram_token: ${MY_TOKEN}",
                "allowed_user_ids: '11,12'",
                "storage:",
                "  provider: obsidian_vault",
                "  obsidian_vault:",
                "    root: ${MY_ROOT}",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(
        config_path=yaml_path,
        cli_overrides={"telegram_token": "${MY_TOKEN}"},
    )
    assert settings.telegram_token == "expanded-token"


@pytest.mark.parametrize("value", ["25:00", "9:30", "abc", "24:00:00"])
def test_load_settings_rejects_invalid_daily_brief_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    """Invalid daily brief values should fail fast with a clear error."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", value)

    with pytest.raises(ValueError, match="DAILY_BRIEF_TIME_UTC"):
        load_settings()


def test_load_settings_rejects_negative_window_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Negative window values should fail fast."""
    _set_common_required_env(monkeypatch)
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "-1")

    with pytest.raises(ValueError, match="MESSAGE_TIMESTAMP_WINDOW_SECONDS"):
        load_settings()


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
    """Tag parser should normalize, deduplicate, and preserve order."""
    assert _parse_tag_choices("family, focus, FAMILY") == ("family", "focus")
    assert _parse_tag_choices(["health", "Health", "hobby"]) == (
        "health",
        "hobby",
    )


@pytest.mark.parametrize("value", ["", "!!!", "bad tag", ["x", "bad tag"], 123])
def test_parse_tag_choices_rejects_invalid_values(value: object) -> None:
    """Invalid tag choices should fail with a clear validation error."""
    with pytest.raises(ValueError, match="TAG_CHOICES"):
        _parse_tag_choices(value)


def test_parse_daily_brief_time_none_and_numeric_zero() -> None:
    """Daily brief parser should support disabled values from varied sources."""
    assert _parse_daily_brief_time_utc(None) is None
    assert _parse_daily_brief_time_utc(0) is None


def test_merge_configs_ignores_none_and_merges_nested() -> None:
    """Nested mappings should merge while None values keep existing data."""
    merged = _merge_configs(
        {
            "storage": {
                "provider": "obsidian_vault",
                "obsidian_vault": {
                    "root": "/a",
                    "secure_file_permissions": True,
                },
            }
        },
        {
            "storage": {
                "obsidian_vault": {
                    "root": None,
                    "secure_file_permissions": False,
                }
            }
        },
    )

    assert merged["storage"]["provider"] == "obsidian_vault"
    assert merged["storage"]["obsidian_vault"]["root"] == "/a"
    assert merged["storage"]["obsidian_vault"]["secure_file_permissions"] is False


def test_resolve_config_path_resolves_regular_paths(tmp_path: Path) -> None:
    """Regular config paths should resolve to absolute canonical paths."""
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    config_file = nested_dir / "config.yaml"
    config_file.write_text("telegram_token: token\n", encoding="utf-8")

    resolved = _resolve_config_path(config_file)

    assert resolved is not None
    assert resolved == config_file.resolve()


def test_resolve_config_path_preserves_dev_fd_paths() -> None:
    """/dev/fd paths should be preserved for descriptor-backed config input."""
    fd_path = Path("/dev/fd/9")

    resolved = _resolve_config_path(fd_path)

    assert resolved == fd_path
