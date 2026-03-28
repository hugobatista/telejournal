"""Configuration models and loading helpers."""

from .constants import (
    DEFAULT_SETTINGS,
    DEFAULT_TAG_CHOICES,
    STORAGE_PROVIDER_GITHUB,
    STORAGE_PROVIDER_OBSIDIAN,
    STORAGE_PROVIDER_ONEDRIVE,
)
from .models import (
    GitHubRepoConfig,
    ObsidianVaultConfig,
    OneDriveConfig,
    Settings,
    StorageSettings,
)
from .merge import merge_configs as _merge_configs
from .parsers import (
    normalize_allowed_user_ids as _normalize_allowed_user_ids,
    parse_allowed_user_ids as _parse_allowed_user_ids,
    parse_daily_brief_time_utc as _parse_daily_brief_time_utc,
    parse_tag_choices as _parse_tag_choices,
)
from .resolver import load_settings

__all__ = [
    "DEFAULT_SETTINGS",
    "DEFAULT_TAG_CHOICES",
    "GitHubRepoConfig",
    "ObsidianVaultConfig",
    "OneDriveConfig",
    "STORAGE_PROVIDER_GITHUB",
    "STORAGE_PROVIDER_OBSIDIAN",
    "STORAGE_PROVIDER_ONEDRIVE",
    "Settings",
    "StorageSettings",
    "_merge_configs",
    "_normalize_allowed_user_ids",
    "_parse_allowed_user_ids",
    "_parse_daily_brief_time_utc",
    "_parse_tag_choices",
    "load_settings",
]
