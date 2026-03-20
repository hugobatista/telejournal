"""Runtime configuration utilities for /settings updates and persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any

import yaml

from telejournal.config import Settings

DEFAULT_CONFIG_FILENAME = "config.yaml"


def format_runtime_config_summary(settings: Settings) -> str:
    """Render a user-facing summary of runtime-editable settings."""
    daily_brief = "0"
    if settings.daily_brief_time_utc is not None:
        daily_brief = settings.daily_brief_time_utc.strftime("%H:%M:%S")

    tags = ", ".join(settings.tag_choices)
    mood_flag = "true" if settings.prompt_for_mood_if_missing else "false"
    bot_menu_flag = "true" if settings.bot_menu_enabled else "false"
    return (
        "Current runtime config:\n"
        f"- tag_choices: {tags}\n"
        f"- daily_brief_time_utc: {daily_brief}\n"
        f"- prompt_for_mood_if_missing: {mood_flag}\n"
        f"- bot_menu_enabled: {bot_menu_flag}"
    )


def apply_runtime_setting(
    settings: Settings,
    key: str,
    value: Any,
) -> tuple[Settings, str]:
    """Return updated settings and a concise confirmation message."""
    if key == "tag_choices":
        parsed = tuple(str(item) for item in value)
        return (
            replace(settings, tag_choices=parsed),
            f"Updated tag_choices: {', '.join(parsed)}",
        )

    if key == "daily_brief_time_utc":
        updated = replace(settings, daily_brief_time_utc=value)
        if value is None:
            return updated, "Updated daily_brief_time_utc: 0 (disabled)"
        return updated, f"Updated daily_brief_time_utc: {value.strftime('%H:%M:%S')}"

    if key == "prompt_for_mood_if_missing":
        enabled = bool(value)
        updated = replace(settings, prompt_for_mood_if_missing=enabled)
        return (
            updated,
            f"Updated prompt_for_mood_if_missing: {'true' if enabled else 'false'}",
        )

    if key == "bot_menu_enabled":
        enabled = bool(value)
        updated = replace(settings, bot_menu_enabled=enabled)
        return (
            updated,
            f"Updated bot_menu_enabled: {'true' if enabled else 'false'}",
        )

    raise ValueError(f"Unsupported config key: {key}")


def _serialize_settings_for_yaml(settings: Settings) -> dict[str, Any]:
    """Serialize full runtime settings into YAML mapping."""
    daily_brief_raw = "0"
    if settings.daily_brief_time_utc is not None:
        daily_brief_raw = settings.daily_brief_time_utc.strftime("%H:%M:%S")

    return {
        "telegram_token": settings.telegram_token,
        "vault_root": str(settings.vault_root),
        "allowed_user_ids": sorted(settings.allowed_user_ids),
        "log_level": settings.log_level,
        "message_timestamp_window_seconds": settings.message_timestamp_window_seconds,
        "secure_file_permissions": settings.secure_file_permissions,
        "daily_brief_time_utc": daily_brief_raw,
        "tag_choices": list(settings.tag_choices),
        "prompt_for_mood_if_missing": settings.prompt_for_mood_if_missing,
        "bot_menu_enabled": settings.bot_menu_enabled,
    }


def persist_runtime_settings(
    settings: Settings,
) -> tuple[Settings, Path, Path | None]:
    """Persist runtime settings to YAML and return updated settings/paths.

    - If a config file was originally used, it is backed up and overwritten.
    - If not, a new ``config.yaml`` is created in the current working directory.
    """
    target_path = settings.config_path
    if target_path is None:
        target_path = (Path.cwd() / DEFAULT_CONFIG_FILENAME).resolve()
    else:
        target_path = target_path.expanduser().resolve()

    target_path.parent.mkdir(parents=True, exist_ok=True)

    backup_path: Path | None = None
    if target_path.exists() and target_path.is_file():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = target_path.with_name(f"{target_path.name}.{stamp}.bak")
        shutil.copy2(target_path, backup_path)

    payload = _serialize_settings_for_yaml(settings)
    rendered = yaml.safe_dump(payload, sort_keys=False)
    target_path.write_text(rendered, encoding="utf-8")

    updated = replace(settings, config_path=target_path)
    return updated, target_path, backup_path
