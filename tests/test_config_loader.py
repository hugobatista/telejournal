"""Tests for configuration source loaders and merge helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from telejournal.config import _merge_configs
from telejournal.config_loader import expand_env_vars, load_env_config, load_yaml_config


def test_expand_env_vars_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables should be expanded recursively."""
    monkeypatch.setenv("TOKEN_VALUE", "abc")
    data = {"token": "${TOKEN_VALUE}", "items": ["${TOKEN_VALUE}", 1]}

    expanded = expand_env_vars(data)

    assert expanded["token"] == "abc"
    assert expanded["items"][0] == "abc"
    assert expanded["items"][1] == 1


def test_load_yaml_config_none() -> None:
    """None config path should yield empty configuration."""
    assert load_yaml_config(None) == {}


def test_load_yaml_config_root_must_be_mapping(tmp_path: Path) -> None:
    """YAML root must be a mapping for settings files."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- not\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="root must be a mapping"):
        load_yaml_config(config_path)


def test_load_yaml_config_empty_file(tmp_path: Path) -> None:
    """Empty YAML files should be treated as empty mappings."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    assert load_yaml_config(config_path) == {}


def test_load_env_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment values should map into settings-compatible keys."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1,2")
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", "09:00")
    monkeypatch.setenv("TAG_CHOICES", "family,health")
    monkeypatch.setenv("PROMPT_FOR_MOOD_IF_MISSING", "false")
    monkeypatch.setenv("BOT_MENU_ENABLED", "false")
    monkeypatch.setenv("STORAGE_PROVIDER", "obsidian_vault")
    monkeypatch.setenv("STORAGE_OBSIDIAN_VAULT_ROOT", "/tmp/vault")
    monkeypatch.setenv(
        "STORAGE_OBSIDIAN_VAULT_SECURE_FILE_PERMISSIONS",
        "false",
    )

    config = load_env_config()

    assert config["telegram_token"] == "x"
    assert config["allowed_user_ids"] == "1,2"
    assert config["daily_brief_time_utc"] == "09:00"
    assert config["tag_choices"] == "family,health"
    assert config["prompt_for_mood_if_missing"] == "false"
    assert config["bot_menu_enabled"] == "false"
    assert config["storage"]["provider"] == "obsidian_vault"
    assert config["storage"]["obsidian_vault"]["root"] == "/tmp/vault"
    assert config["storage"]["obsidian_vault"]["secure_file_permissions"] == "false"


def test_merge_configs_ignores_none() -> None:
    """None values should not overwrite an already merged value."""
    merged = _merge_configs(
        {"token": "default", "level": "INFO"},
        {"token": None, "level": "DEBUG"},
    )
    assert merged["token"] == "default"
    assert merged["level"] == "DEBUG"


def test_load_env_config_github_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub storage environment variables should map to nested config keys."""
    monkeypatch.setenv("STORAGE_PROVIDER", "github_repo")
    monkeypatch.setenv("STORAGE_GITHUB_OWNER", "acme")
    monkeypatch.setenv("STORAGE_GITHUB_REPO", "journal")
    monkeypatch.setenv("STORAGE_GITHUB_BRANCH", "dev")
    monkeypatch.setenv("STORAGE_GITHUB_TOKEN", "token")
    monkeypatch.setenv("STORAGE_GITHUB_PATH_PREFIX", "notes")
    monkeypatch.setenv("STORAGE_GITHUB_API_BASE_URL", "https://api.github.com")
    monkeypatch.setenv("STORAGE_GITHUB_BATCH_WINDOW_SECONDS", "90")

    config = load_env_config()

    assert config["storage"]["provider"] == "github_repo"
    assert config["storage"]["github_repo"]["owner"] == "acme"
    assert config["storage"]["github_repo"]["repo"] == "journal"
    assert config["storage"]["github_repo"]["branch"] == "dev"
    assert config["storage"]["github_repo"]["token"] == "token"
    assert config["storage"]["github_repo"]["path_prefix"] == "notes"
    assert config["storage"]["github_repo"]["api_base_url"] == "https://api.github.com"
    assert config["storage"]["github_repo"]["batch_window_seconds"] == "90"


def test_load_env_config_onedrive_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """OneDrive storage env variables should map to nested config keys."""
    monkeypatch.setenv("STORAGE_PROVIDER", "onedrive")
    monkeypatch.setenv("STORAGE_ONEDRIVE_TENANT_ID", "common")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ROOT_PATH", "Apps/telejournal")
    monkeypatch.setenv(
        "STORAGE_ONEDRIVE_API_BASE_URL",
        "https://graph.microsoft.com/v1.0",
    )
    monkeypatch.setenv("STORAGE_ONEDRIVE_BATCH_WINDOW_SECONDS", "60")
    monkeypatch.setenv("STORAGE_ONEDRIVE_ACCESS_TOKEN", "access")
    monkeypatch.setenv("STORAGE_ONEDRIVE_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv(
        "STORAGE_ONEDRIVE_TOKEN_EXPIRES_AT_UTC",
        "2026-03-28T10:00:00Z",
    )

    config = load_env_config()

    assert config["storage"]["provider"] == "onedrive"
    assert config["storage"]["onedrive"]["tenant_id"] == "common"
    assert config["storage"]["onedrive"]["client_id"] == "client-id"
    assert config["storage"]["onedrive"]["client_secret"] == "client-secret"
    assert config["storage"]["onedrive"]["root_path"] == "Apps/telejournal"
    assert (
        config["storage"]["onedrive"]["api_base_url"]
        == "https://graph.microsoft.com/v1.0"
    )
    assert config["storage"]["onedrive"]["batch_window_seconds"] == "60"
    assert config["storage"]["onedrive"]["access_token"] == "access"
    assert config["storage"]["onedrive"]["refresh_token"] == "refresh"
