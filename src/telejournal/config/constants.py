"""Configuration constants and defaults for Telejournal."""

from __future__ import annotations

from typing import Any

DEFAULT_TAG_CHOICES: tuple[str, ...] = (
    "family",
    "health",
    "love",
    "hobby",
    "other",
    "finance",
    "social",
)

STORAGE_PROVIDER_OBSIDIAN = "obsidian_vault"
STORAGE_PROVIDER_GITHUB = "github_repo"
STORAGE_PROVIDER_ONEDRIVE = "onedrive"

DEFAULT_SETTINGS: dict[str, Any] = {
    "log_level": "INFO",
    "message_timestamp_window_seconds": 60,
    "daily_brief_time_utc": "09:00",
    "tag_choices": list(DEFAULT_TAG_CHOICES),
    "prompt_for_mood_if_missing": True,
    "bot_menu_enabled": True,
    "storage": {
        "provider": STORAGE_PROVIDER_OBSIDIAN,
        "obsidian_vault": {
            "secure_file_permissions": True,
        },
        "github_repo": {
            "branch": "main",
            "path_prefix": "",
            "api_base_url": "https://api.github.com",
            "batch_window_seconds": 60,
        },
        "onedrive": {
            "tenant_id": "common",
            "client_secret": None,
            "root_path": "Apps/telejournal",
            "api_base_url": "https://graph.microsoft.com/v1.0",
            "batch_window_seconds": 60,
        },
    },
}
