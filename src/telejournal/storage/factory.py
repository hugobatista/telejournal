"""Factory helpers for runtime storage provider selection."""

from __future__ import annotations

from telejournal.config import (
    STORAGE_PROVIDER_GITHUB,
    STORAGE_PROVIDER_GOOGLEDRIVE,
    STORAGE_PROVIDER_OBSIDIAN,
    STORAGE_PROVIDER_ONEDRIVE,
    Settings,
)

from .github import GitHubRepository
from .google_drive import GoogleDriveRepository
from .obsidian import VaultRepository
from .onedrive import OneDriveRepository


def build_repository(
    settings: Settings,
) -> VaultRepository | GitHubRepository | OneDriveRepository | GoogleDriveRepository:
    """Build a storage repository from runtime provider settings."""
    if settings.storage_provider == STORAGE_PROVIDER_OBSIDIAN:
        return VaultRepository(
            settings.vault_root,
            secure_permissions=settings.secure_file_permissions,
        )
    if settings.storage_provider == STORAGE_PROVIDER_GITHUB:
        if (
            settings.github_owner is None
            or settings.github_repo is None
            or settings.github_token is None
        ):
            raise ValueError("GitHub storage settings are incomplete")
        return GitHubRepository(
            owner=settings.github_owner,
            repo=settings.github_repo,
            token=settings.github_token,
            branch=settings.github_branch,
            path_prefix=settings.github_path_prefix,
            api_base_url=settings.github_api_base_url,
            batch_window_seconds=settings.github_batch_window_seconds,
        )
    if settings.storage_provider == STORAGE_PROVIDER_ONEDRIVE:
        if (
            settings.onedrive_client_id is None
            or settings.onedrive_client_secret is None
        ):
            raise ValueError("OneDrive storage settings are incomplete")
        return OneDriveRepository(
            tenant_id=settings.onedrive_tenant_id,
            client_id=settings.onedrive_client_id,
            client_secret=settings.onedrive_client_secret,
            root_path=settings.onedrive_root_path,
            api_base_url=settings.onedrive_api_base_url,
            batch_window_seconds=settings.onedrive_batch_window_seconds,
            access_token=settings.onedrive_access_token,
            refresh_token=settings.onedrive_refresh_token,
            token_expires_at_utc=settings.onedrive_token_expires_at_utc,
            config_path=settings.config_path,
        )
    if settings.storage_provider == STORAGE_PROVIDER_GOOGLEDRIVE:
        if (
            settings.google_drive_client_id is None
            or settings.google_drive_client_secret is None
        ):
            raise ValueError("Google Drive storage settings are incomplete")
        return GoogleDriveRepository(
            client_id=settings.google_drive_client_id,
            client_secret=settings.google_drive_client_secret,
            folder_id=settings.google_drive_folder_id or "",
            batch_window_seconds=settings.google_drive_batch_window_seconds,
            access_token=settings.google_drive_access_token,
            refresh_token=settings.google_drive_refresh_token,
            token_expires_at_utc=settings.google_drive_token_expires_at_utc,
            config_path=settings.config_path,
        )
    raise ValueError(f"Unsupported storage provider: {settings.storage_provider}")
