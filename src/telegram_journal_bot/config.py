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
    log_level: str = "INFO"
    allowed_user_ids: set[int] | None = None


def _parse_allowed_user_ids(raw_value: str | None) -> set[int] | None:
    """Parse optional comma-separated Telegram user IDs."""
    if not raw_value:
        return None

    parsed: set[int] = set()
    for part in raw_value.split(","):
        value = part.strip()
        if not value:
            continue
        parsed.add(int(value))
    return parsed or None


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

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    allowed_user_ids = _parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))

    return Settings(
        telegram_token=token,
        vault_root=vault_root,
        log_level=log_level,
        allowed_user_ids=allowed_user_ids,
    )
