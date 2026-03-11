"""Configuration loading for the Telegram journal bot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telejournal.config_loader import expand_env_vars, load_env_config, load_yaml_config


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved from environment variables."""

    telegram_token: str
    vault_root: Path
    allowed_user_ids: set[int]
    log_level: str = "INFO"
    message_timestamp_window_seconds: int = 60
    secure_file_permissions: bool = True


DEFAULT_SETTINGS: dict[str, Any] = {
    "log_level": "INFO",
    "message_timestamp_window_seconds": 60,
    "secure_file_permissions": True,
}


def _parse_allowed_user_ids(raw_value: str) -> set[int]:
    """Parse comma-separated Telegram user IDs."""
    parsed: set[int] = set()
    for part in raw_value.split(","):
        value = part.strip()
        if not value:
            continue
        parsed.add(int(value))
    if not parsed:
        raise ValueError(
            "TELEGRAM_ALLOWED_USER_IDS must contain at least one valid user ID"
        )
    return parsed


def _parse_bool(raw_value: str | bool) -> bool:
    """Parse bool-like values from strings or booleans."""
    if isinstance(raw_value, bool):
        return raw_value
    return raw_value.strip().lower() in ("true", "1", "yes", "on")


def _normalize_allowed_user_ids(raw_value: Any) -> set[int]:
    """Normalize allowed user IDs from string/list/set values."""
    if isinstance(raw_value, str):
        return _parse_allowed_user_ids(raw_value)
    if isinstance(raw_value, (list, set, tuple)):
        parsed = {int(value) for value in raw_value}
        if not parsed:
            raise ValueError(
                "TELEGRAM_ALLOWED_USER_IDS must contain at least one valid user ID"
            )
        return parsed
    raise ValueError("TELEGRAM_ALLOWED_USER_IDS must be a CSV string or list")


def _resolve_config_path(config_path: Path | None) -> Path | None:
    """Resolve config path to an absolute path when provided."""
    if config_path is None:
        return None
    return config_path.expanduser().resolve()


def _merge_configs(*sources: dict[str, Any]) -> dict[str, Any]:
    """Merge config dictionaries from lowest to highest priority.
    
    Iterates through sources left-to-right, skipping None values.
    Later sources override earlier ones.
    """
    merged: dict[str, Any] = {}
    for source in sources:
        for key, value in source.items():
            if value is not None:
                merged[key] = value
    return merged


def load_settings(
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Settings:
    """Load settings from defaults, env, YAML, and CLI with priority order."""
    yaml_path = _resolve_config_path(config_path)
    yaml_config = load_yaml_config(yaml_path)
    env_config = load_env_config()
    cli_config = expand_env_vars(cli_overrides or {})

    merged = _merge_configs(DEFAULT_SETTINGS, env_config, yaml_config, cli_config)

    token = str(merged.get("telegram_token", "")).strip()
    if not token:
        raise ValueError("TELEGRAM_TOKEN is required")

    vault_root_raw = str(merged.get("vault_root", "")).strip()
    if not vault_root_raw:
        raise ValueError("VAULT_ROOT is required")
    vault_root = Path(vault_root_raw).expanduser().resolve()
    vault_root.mkdir(parents=True, exist_ok=True)

    allowed_raw = merged.get("allowed_user_ids", "")
    if not allowed_raw:
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS is required")
    allowed_user_ids = _normalize_allowed_user_ids(allowed_raw)

    log_level = str(merged.get("log_level", "INFO")).strip().upper() or "INFO"
    window_seconds = int(merged.get("message_timestamp_window_seconds", 60))
    if window_seconds < 0:
        raise ValueError("MESSAGE_TIMESTAMP_WINDOW_SECONDS must be >= 0")

    secure_permissions = _parse_bool(merged.get("secure_file_permissions", True))

    return Settings(
        telegram_token=token,
        vault_root=vault_root,
        allowed_user_ids=allowed_user_ids,
        log_level=log_level,
        message_timestamp_window_seconds=window_seconds,
        secure_file_permissions=secure_permissions,
    )
