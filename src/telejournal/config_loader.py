"""Configuration source loaders for Telejournal."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import cast

import yaml


def expand_env_vars(data: Any) -> Any:
    """Recursively expand environment variables in config values."""
    if isinstance(data, str):
        return os.path.expandvars(data)
    if isinstance(data, dict):
        return {key: expand_env_vars(value) for key, value in data.items()}
    if isinstance(data, list):
        return [expand_env_vars(value) for value in data]
    return data


def load_yaml_config(config_path: Path | None) -> dict[str, Any]:
    """Load and expand a YAML configuration file when provided."""
    if config_path is None:
        return {}

    with config_path.open("r", encoding="utf-8") as file_handle:
        raw_config = yaml.safe_load(file_handle)

    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise ValueError("YAML configuration root must be a mapping")

    expanded = expand_env_vars(raw_config)
    return cast(dict[str, Any], expanded)


def load_env_config() -> dict[str, Any]:
    """Load settings-like values from environment variables."""
    config: dict[str, Any] = {}

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if token:
        config["telegram_token"] = token

    vault_root = os.getenv("VAULT_ROOT", "").strip()
    if vault_root:
        config["vault_root"] = vault_root

    allowed_user_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if allowed_user_ids:
        config["allowed_user_ids"] = allowed_user_ids

    log_level = os.getenv("LOG_LEVEL", "").strip()
    if log_level:
        config["log_level"] = log_level

    window_seconds = os.getenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "").strip()
    if window_seconds:
        config["message_timestamp_window_seconds"] = window_seconds

    secure_permissions = os.getenv("SECURE_FILE_PERMISSIONS", "").strip()
    if secure_permissions:
        config["secure_file_permissions"] = secure_permissions

    daily_brief_time_utc = os.getenv("DAILY_BRIEF_TIME_UTC", "").strip()
    if daily_brief_time_utc:
        config["daily_brief_time_utc"] = daily_brief_time_utc

    expanded = expand_env_vars(config)
    return cast(dict[str, Any], expanded)
