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
    monkeypatch.setenv("VAULT_ROOT", "/tmp/vault")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1,2")
    monkeypatch.setenv("DAILY_BRIEF_TIME_UTC", "09:00")
    monkeypatch.setenv("TAG_CHOICES", "family,health")
    monkeypatch.setenv("PROMPT_FOR_MOOD_IF_MISSING", "false")
    monkeypatch.setenv("BOT_MENU_ENABLED", "false")

    config = load_env_config()

    assert config["telegram_token"] == "x"
    assert config["vault_root"] == "/tmp/vault"
    assert config["allowed_user_ids"] == "1,2"
    assert config["daily_brief_time_utc"] == "09:00"
    assert config["tag_choices"] == "family,health"
    assert config["prompt_for_mood_if_missing"] == "false"
    assert config["bot_menu_enabled"] == "false"


def test_merge_configs_ignores_none() -> None:
    """None values should not overwrite an already merged value."""
    merged = _merge_configs(
        {"token": "default", "level": "INFO"},
        {"token": None, "level": "DEBUG"},
    )
    assert merged["token"] == "default"
    assert merged["level"] == "DEBUG"
