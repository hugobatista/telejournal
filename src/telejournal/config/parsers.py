"""Configuration parsing helpers."""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
import re
from typing import Any

_TAG_CHOICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def parse_allowed_user_ids(raw_value: str) -> set[int]:
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


def parse_bool(raw_value: str | bool) -> bool:
    """Parse bool-like values from strings or booleans."""
    if isinstance(raw_value, bool):
        return raw_value
    return raw_value.strip().lower() in ("true", "1", "yes", "on")


def normalize_allowed_user_ids(raw_value: Any) -> set[int]:
    """Normalize allowed user IDs from string/list/set values."""
    if isinstance(raw_value, str):
        return parse_allowed_user_ids(raw_value)
    if isinstance(raw_value, (list, set, tuple)):
        parsed = {int(value) for value in raw_value}
        if not parsed:
            raise ValueError(
                "TELEGRAM_ALLOWED_USER_IDS must contain at least one valid user ID"
            )
        return parsed
    raise ValueError("TELEGRAM_ALLOWED_USER_IDS must be a CSV string or list")


def parse_daily_brief_time_utc(raw_value: Any) -> time | None:
    """Parse daily brief time in UTC using HH:MM, HH:MM:SS, or 0 (disabled)."""
    if raw_value is None:
        return None

    if isinstance(raw_value, str):
        value = raw_value.strip()
    else:
        value = str(raw_value).strip()

    if value == "0":
        return None

    if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value):
        raise ValueError("DAILY_BRIEF_TIME_UTC must be '0', 'HH:MM', or 'HH:MM:SS'")

    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt).time()
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue

    raise ValueError("DAILY_BRIEF_TIME_UTC must be '0', 'HH:MM', or 'HH:MM:SS'")


def parse_tag_choices(raw_value: Any) -> tuple[str, ...]:
    """Parse tag choices from CSV string or list-like values."""
    raw_items: list[Any]
    if isinstance(raw_value, str):
        raw_items = raw_value.split(",")
    elif isinstance(raw_value, (list, tuple, set)):
        raw_items = list(raw_value)
    else:
        raise ValueError("TAG_CHOICES must be a CSV string or list")

    parsed: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        tag = str(raw_item).strip().lower()
        if not tag:
            continue
        if not _TAG_CHOICE_RE.fullmatch(tag):
            raise ValueError("TAG_CHOICES items must match ^[a-z0-9][a-z0-9_-]{0,31}$")
        if tag in seen:
            continue
        seen.add(tag)
        parsed.append(tag)

    if not parsed:
        raise ValueError("TAG_CHOICES must contain at least one valid tag")

    return tuple(parsed)


def resolve_config_path(config_path: Path | None) -> Path | None:
    """Resolve config path to an absolute path when provided."""
    if config_path is None:
        return None
    return config_path.expanduser().resolve()


def normalize_onedrive_root_path(raw_value: Any) -> str:
    """Normalize OneDrive root path to a clean slash-separated segment."""
    value = str(raw_value or "").strip().replace("\\", "/")
    return value.strip("/")


def normalize_path_prefix(raw_value: Any) -> str:
    """Normalize optional GitHub path prefix to a clean relative segment."""
    value = str(raw_value or "").strip().replace("\\", "/")
    value = value.strip("/")
    if value in ("", "."):
        return ""
    return value
