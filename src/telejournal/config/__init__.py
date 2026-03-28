"""Configuration models and loading helpers."""

from .constants import (
    DEFAULT_SETTINGS,
    DEFAULT_TAG_CHOICES,
    STORAGE_PROVIDER_GITHUB,
    STORAGE_PROVIDER_GOOGLEDRIVE,
    STORAGE_PROVIDER_OBSIDIAN,
    STORAGE_PROVIDER_ONEDRIVE,
)
from .merge import merge_configs as _merge_configs
from .models import (
    GitHubRepoConfig,
    GoogleDriveConfig,
    ObsidianVaultConfig,
    OneDriveConfig,
    Settings,
    StorageSettings,
)
from .parsers import (
    normalize_allowed_user_ids as _normalize_allowed_user_ids,
    parse_allowed_user_ids as _parse_allowed_user_ids,
    parse_daily_brief_time_utc as _parse_daily_brief_time_utc,
    parse_tag_choices as _parse_tag_choices,
    resolve_config_path as _resolve_config_path,
)
from .resolver import load_settings

__all__ = [
    "DEFAULT_SETTINGS",
    "DEFAULT_TAG_CHOICES",
    "GitHubRepoConfig",
    "GoogleDriveConfig",
    "ObsidianVaultConfig",
    "OneDriveConfig",
    "STORAGE_PROVIDER_GITHUB",
    "STORAGE_PROVIDER_GOOGLEDRIVE",
    "STORAGE_PROVIDER_OBSIDIAN",
    "STORAGE_PROVIDER_ONEDRIVE",
    "Settings",
    "StorageSettings",
    "_merge_configs",
    "_normalize_allowed_user_ids",
    "_parse_allowed_user_ids",
    "_parse_daily_brief_time_utc",
    "_parse_tag_choices",
    "_resolve_config_path",
    "load_settings",
]
