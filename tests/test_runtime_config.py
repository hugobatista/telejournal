"""Tests for runtime configuration helpers and persistence."""

from __future__ import annotations

from datetime import UTC, time
from pathlib import Path

import pytest
import yaml

from telejournal.config import Settings
from telejournal.runtime_config import (
    DEFAULT_CONFIG_FILENAME,
    apply_runtime_setting,
    format_runtime_config_summary,
    persist_runtime_settings,
)


def test_format_runtime_config_summary_with_enabled_brief(tmp_path: Path) -> None:
    """Summary should render active daily brief time and mood prompt flag."""
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        daily_brief_time_utc=time(9, 30, tzinfo=UTC),
        tag_choices=("family", "focus"),
        prompt_for_mood_if_missing=False,
        bot_menu_enabled=False,
    )

    summary = format_runtime_config_summary(settings)
    assert "09:30:00" in summary
    assert "family, focus" in summary
    assert "false" in summary
    assert "bot_menu_enabled" in summary


def test_apply_runtime_setting_supported_keys(tmp_path: Path) -> None:
    """Runtime setting application should support each configurable key."""
    settings = Settings("token", tmp_path, {1})

    updated_tags, msg_tags = apply_runtime_setting(
        settings,
        "tag_choices",
        ("a", "b"),
    )
    assert updated_tags.tag_choices == ("a", "b")
    assert "tag_choices" in msg_tags

    updated_brief_none, msg_brief_none = apply_runtime_setting(
        settings,
        "daily_brief_time_utc",
        None,
    )
    assert updated_brief_none.daily_brief_time_utc is None
    assert "disabled" in msg_brief_none

    updated_brief_time, msg_brief_time = apply_runtime_setting(
        settings,
        "daily_brief_time_utc",
        time(7, 45, tzinfo=UTC),
    )
    assert updated_brief_time.daily_brief_time_utc == time(7, 45, tzinfo=UTC)
    assert "07:45:00" in msg_brief_time

    updated_prompt, msg_prompt = apply_runtime_setting(
        settings,
        "prompt_for_mood_if_missing",
        False,
    )
    assert updated_prompt.prompt_for_mood_if_missing is False
    assert "false" in msg_prompt

    updated_menu, msg_menu = apply_runtime_setting(
        settings,
        "bot_menu_enabled",
        False,
    )
    assert updated_menu.bot_menu_enabled is False
    assert "false" in msg_menu


def test_apply_runtime_setting_rejects_unknown_key(tmp_path: Path) -> None:
    """Unsupported runtime keys should raise a clear ValueError."""
    settings = Settings("token", tmp_path, {1})
    with pytest.raises(ValueError, match="Unsupported config key"):
        apply_runtime_setting(settings, "unknown", "value")


def test_persist_runtime_settings_creates_default_config_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no config path is known, persistence should create ./config.yaml."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path / "vault",
        allowed_user_ids={3, 1},
        tag_choices=("family", "focus"),
        prompt_for_mood_if_missing=True,
        bot_menu_enabled=False,
        config_path=None,
    )

    persisted, target_path, backup_path = persist_runtime_settings(settings)

    assert target_path == (tmp_path / DEFAULT_CONFIG_FILENAME).resolve()
    assert backup_path is None
    assert persisted.config_path == target_path

    payload = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    assert payload["telegram_token"] == "token"
    assert payload["storage"]["provider"] == "obsidian_vault"
    assert payload["storage"]["obsidian_vault"]["root"] == str(tmp_path / "vault")
    assert payload["allowed_user_ids"] == [1, 3]
    assert payload["tag_choices"] == ["family", "focus"]
    assert payload["prompt_for_mood_if_missing"] is True
    assert payload["bot_menu_enabled"] is False


def test_persist_runtime_settings_backs_up_existing_yaml(tmp_path: Path) -> None:
    """Existing config files should be backed up before overwrite."""
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("telegram_token: old\n", encoding="utf-8")
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path / "vault",
        allowed_user_ids={1},
        daily_brief_time_utc=time(8, 0, tzinfo=UTC),
        config_path=config_path,
    )

    persisted, target_path, backup_path = persist_runtime_settings(settings)

    assert target_path == config_path.resolve()
    assert persisted.config_path == target_path
    assert backup_path is not None
    assert backup_path.exists()
    assert "telegram_token: old" in backup_path.read_text(encoding="utf-8")


def test_persist_runtime_settings_handles_relative_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative config paths should be resolved from current directory."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path / "vault",
        allowed_user_ids={1},
        config_path=Path("nested") / "config.yaml",
    )

    persisted, target_path, backup_path = persist_runtime_settings(settings)

    assert backup_path is None
    assert target_path == (tmp_path / "nested" / "config.yaml").resolve()
    assert persisted.config_path == target_path


def test_persist_runtime_settings_serializes_github_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistence should keep github storage configuration hierarchy."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        storage_provider="github_repo",
        github_owner="acme",
        github_repo="journal",
        github_token="token",
        github_branch="dev",
        github_path_prefix="notes",
        github_api_base_url="https://api.github.com",
        github_batch_window_seconds=180,
    )

    _persisted, target_path, _backup_path = persist_runtime_settings(settings)
    payload = yaml.safe_load(target_path.read_text(encoding="utf-8"))

    assert payload["storage"]["provider"] == "github_repo"
    assert payload["storage"]["github_repo"]["owner"] == "acme"
    assert payload["storage"]["github_repo"]["repo"] == "journal"
    assert payload["storage"]["github_repo"]["token"] == "token"
    assert payload["storage"]["github_repo"]["batch_window_seconds"] == 180


def test_persist_runtime_settings_serializes_onedrive_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistence should keep onedrive storage configuration hierarchy."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        storage_provider="onedrive",
        onedrive_tenant_id="common",
        onedrive_client_id="client-id",
        onedrive_client_secret="client-secret",
        onedrive_root_path="Apps/telejournal",
        onedrive_api_base_url="https://graph.microsoft.com/v1.0",
        onedrive_batch_window_seconds=90,
        onedrive_access_token="access",
        onedrive_refresh_token="refresh",
        onedrive_token_expires_at_utc="2026-03-28T10:00:00Z",
    )

    _persisted, target_path, _backup_path = persist_runtime_settings(settings)
    payload = yaml.safe_load(target_path.read_text(encoding="utf-8"))

    assert payload["storage"]["provider"] == "onedrive"
    assert payload["storage"]["onedrive"]["tenant_id"] == "common"
    assert payload["storage"]["onedrive"]["client_id"] == "client-id"
    assert payload["storage"]["onedrive"]["client_secret"] == "client-secret"
    assert payload["storage"]["onedrive"]["root_path"] == "Apps/telejournal"
    assert payload["storage"]["onedrive"]["batch_window_seconds"] == 90


def test_persist_runtime_settings_serializes_google_drive_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistence should keep google drive storage configuration hierarchy."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        storage_provider="google_drive",
        google_drive_client_id="client-id",
        google_drive_client_secret="client-secret",
        google_drive_folder_id="folder-id",
        google_drive_batch_window_seconds=75,
        google_drive_access_token="access",
        google_drive_refresh_token="refresh",
        google_drive_token_expires_at_utc="2026-03-28T10:00:00Z",
    )

    _persisted, target_path, _backup_path = persist_runtime_settings(settings)
    payload = yaml.safe_load(target_path.read_text(encoding="utf-8"))

    assert payload["storage"]["provider"] == "google_drive"
    assert payload["storage"]["google_drive"]["client_id"] == "client-id"
    assert payload["storage"]["google_drive"]["client_secret"] == "client-secret"
    assert payload["storage"]["google_drive"]["folder_id"] == "folder-id"
    assert payload["storage"]["google_drive"]["batch_window_seconds"] == 75
