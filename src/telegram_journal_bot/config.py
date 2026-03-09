"""Configuration loading for the Telegram journal bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved from environment variables."""

    telegram_token: str
    vault_root: Path
    allowed_user_ids: set[int]
    log_level: str = "INFO"
    message_timestamp_window_seconds: int = 60
    secure_file_permissions: bool = True


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


def load_settings() -> Settings:
    """Load settings from environment and validate required values."""
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_TOKEN is required")

    vault_root_raw = os.getenv("VAULT_ROOT", "").strip()
    if not vault_root_raw:
        raise ValueError("VAULT_ROOT is required")

    vault_root = Path(vault_root_raw).expanduser().resolve()
    vault_root.mkdir(parents=True, exist_ok=True)

    allowed_user_ids_raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not allowed_user_ids_raw:
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS is required")
    allowed_user_ids = _parse_allowed_user_ids(allowed_user_ids_raw)

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    window_seconds = int(
        os.getenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "60").strip() or "60"
    )
    if window_seconds < 0:
        raise ValueError("MESSAGE_TIMESTAMP_WINDOW_SECONDS must be >= 0")

    # SECURITY: Parse secure file permissions setting (enabled by default)
    secure_permissions_raw = (
        os.getenv("SECURE_FILE_PERMISSIONS", "true").strip().lower()
    )
    secure_permissions = secure_permissions_raw in ("true", "1", "yes", "on")

    return Settings(
        telegram_token=token,
        vault_root=vault_root,
        allowed_user_ids=allowed_user_ids,
        log_level=log_level,
        message_timestamp_window_seconds=window_seconds,
        secure_file_permissions=secure_permissions,
    )
