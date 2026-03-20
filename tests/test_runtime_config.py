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
    assert payload["vault_root"] == str((tmp_path / "vault"))
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
