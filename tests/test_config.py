"""Tests for environment configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from telegram_journal_bot.config import _parse_allowed_user_ids, load_settings


def test_parse_allowed_user_ids_none() -> None:
    """Empty values should disable whitelist enforcement."""
    assert _parse_allowed_user_ids(None) is None
    assert _parse_allowed_user_ids("") is None


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


def test_load_settings_builds_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Environment values should be normalized to runtime settings."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1, 2")

    settings = load_settings()

    assert settings.telegram_token == "token"
    assert settings.vault_root.exists()
    assert settings.log_level == "DEBUG"
    assert settings.allowed_user_ids == {1, 2}
