"""Configuration data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from .constants import (
    DEFAULT_TAG_CHOICES,
    STORAGE_PROVIDER_OBSIDIAN,
)


@dataclass(frozen=True)
class ObsidianVaultConfig:
    """Settings for local Obsidian vault storage."""

    root: Path
    secure_file_permissions: bool = True


@dataclass(frozen=True)
class GitHubRepoConfig:
    """Settings for GitHub repository storage."""

    owner: str
    repo: str
    token: str
    branch: str = "main"
    path_prefix: str = ""
    api_base_url: str = "https://api.github.com"
    batch_window_seconds: int = 60


@dataclass(frozen=True)
class OneDriveConfig:
    """Settings for OneDrive storage."""

    tenant_id: str = "common"
    client_id: str | None = None
    client_secret: str | None = None
    root_path: str = "Apps/telejournal"
    api_base_url: str = "https://graph.microsoft.com/v1.0"
    batch_window_seconds: int = 60
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at_utc: str | None = None


@dataclass(frozen=True)
class StorageSettings:
    """Normalized storage provider configuration."""

    provider: str = STORAGE_PROVIDER_OBSIDIAN
    obsidian_vault: ObsidianVaultConfig | None = None
    github_repo: GitHubRepoConfig | None = None
    onedrive: OneDriveConfig | None = None


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved from environment and file configuration."""

    telegram_token: str
    vault_root: Path
    allowed_user_ids: set[int]
    log_level: str = "INFO"
    message_timestamp_window_seconds: int = 60
    secure_file_permissions: bool = True
    daily_brief_time_utc: time | None = None
    tag_choices: tuple[str, ...] = DEFAULT_TAG_CHOICES
    prompt_for_mood_if_missing: bool = True
    bot_menu_enabled: bool = True
    config_path: Path | None = None
    storage_provider: str = STORAGE_PROVIDER_OBSIDIAN
    github_owner: str | None = None
    github_repo: str | None = None
    github_branch: str = "main"
    github_token: str | None = None
    github_path_prefix: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_batch_window_seconds: int = 60
    onedrive_tenant_id: str = "common"
    onedrive_client_id: str | None = None
    onedrive_client_secret: str | None = None
    onedrive_root_path: str = "Apps/telejournal"
    onedrive_api_base_url: str = "https://graph.microsoft.com/v1.0"
    onedrive_batch_window_seconds: int = 60
    onedrive_access_token: str | None = None
    onedrive_refresh_token: str | None = None
    onedrive_token_expires_at_utc: str | None = None
    storage: StorageSettings | None = field(default=None)
