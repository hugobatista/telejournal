"""Settings resolver from defaults, env, YAML, and CLI values."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from telejournal.config_loader import (
    expand_env_vars,
    load_env_config,
    load_yaml_config,
)

from .constants import (
    DEFAULT_SETTINGS,
    STORAGE_PROVIDER_GITHUB,
    STORAGE_PROVIDER_GOOGLEDRIVE,
    STORAGE_PROVIDER_OBSIDIAN,
    STORAGE_PROVIDER_ONEDRIVE,
)
from .merge import merge_configs, storage_node
from .models import (
    GitHubRepoConfig,
    GoogleDriveConfig,
    ObsidianVaultConfig,
    OneDriveConfig,
    Settings,
    StorageSettings,
)
from .parsers import (
    normalize_allowed_user_ids,
    normalize_onedrive_root_path,
    normalize_path_prefix,
    parse_bool,
    parse_daily_brief_time_utc,
    parse_tag_choices,
    resolve_config_path,
)


def load_settings(
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Settings:
    """Load settings from defaults, env, YAML, and CLI with priority order."""
    yaml_path = resolve_config_path(config_path)
    yaml_config = load_yaml_config(yaml_path)
    env_config = load_env_config()
    cli_config = expand_env_vars(cli_overrides or {})

    merged = merge_configs(DEFAULT_SETTINGS, env_config, yaml_config, cli_config)

    token = str(merged.get("telegram_token", "")).strip()
    if not token:
        raise ValueError("TELEGRAM_TOKEN is required")

    allowed_raw = merged.get("allowed_user_ids", "")
    if not allowed_raw:
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS is required")
    allowed_user_ids = normalize_allowed_user_ids(allowed_raw)

    storage = storage_node(merged)
    storage_provider = str(storage.get("provider") or "").strip().lower()
    if storage_provider not in (
        STORAGE_PROVIDER_OBSIDIAN,
        STORAGE_PROVIDER_GITHUB,
        STORAGE_PROVIDER_ONEDRIVE,
        STORAGE_PROVIDER_GOOGLEDRIVE,
    ):
        raise ValueError(
            "storage.provider must be 'obsidian_vault', 'github_repo', "
            "'onedrive', or 'google_drive'"
        )

    vault_root = Path(".").resolve()
    secure_permissions = True
    github_owner: str | None = None
    github_repo: str | None = None
    github_branch = "main"
    github_token: str | None = None
    github_path_prefix = ""
    github_api_base_url = "https://api.github.com"
    github_batch_window_seconds = 60
    onedrive_tenant_id = "common"
    onedrive_client_id: str | None = None
    onedrive_client_secret: str | None = None
    onedrive_root_path = "Apps/telejournal"
    onedrive_api_base_url = "https://graph.microsoft.com/v1.0"
    onedrive_batch_window_seconds = 60
    onedrive_access_token: str | None = None
    onedrive_refresh_token: str | None = None
    onedrive_token_expires_at_utc: str | None = None
    google_drive_client_id: str | None = None
    google_drive_client_secret: str | None = None
    google_drive_folder_id: str | None = None
    google_drive_batch_window_seconds = 60
    google_drive_access_token: str | None = None
    google_drive_refresh_token: str | None = None
    google_drive_token_expires_at_utc: str | None = None

    storage_settings: StorageSettings

    if storage_provider == STORAGE_PROVIDER_OBSIDIAN:
        obsidian = storage["obsidian_vault"]
        root_raw = str(obsidian.get("root", "")).strip()
        if not root_raw:
            raise ValueError(
                "storage.obsidian_vault.root is required for obsidian_vault provider"
            )
        vault_root = Path(root_raw).expanduser().resolve()
        vault_root.mkdir(parents=True, exist_ok=True)
        secure_permissions = parse_bool(obsidian.get("secure_file_permissions", True))
        storage_settings = StorageSettings(
            provider=storage_provider,
            obsidian_vault=ObsidianVaultConfig(
                root=vault_root,
                secure_file_permissions=secure_permissions,
            ),
        )
    elif storage_provider == STORAGE_PROVIDER_GITHUB:
        github = storage["github_repo"]
        github_owner = str(github.get("owner", "")).strip()
        github_repo = str(github.get("repo", "")).strip()
        github_token = str(github.get("token", "")).strip()
        github_branch = str(github.get("branch", "main")).strip() or "main"
        github_path_prefix = normalize_path_prefix(github.get("path_prefix", ""))
        github_api_base_url = (
            str(github.get("api_base_url", "https://api.github.com")).strip()
            or "https://api.github.com"
        )
        github_batch_window_seconds = int(github.get("batch_window_seconds", 60))
        if not github_owner:
            raise ValueError(
                "storage.github_repo.owner is required for github_repo provider"
            )
        if not github_repo:
            raise ValueError(
                "storage.github_repo.repo is required for github_repo provider"
            )
        if not github_token:
            raise ValueError(
                "storage.github_repo.token is required for github_repo provider"
            )
        if github_batch_window_seconds < 1:
            raise ValueError("storage.github_repo.batch_window_seconds must be >= 1")

        storage_settings = StorageSettings(
            provider=storage_provider,
            github_repo=GitHubRepoConfig(
                owner=github_owner,
                repo=github_repo,
                token=github_token,
                branch=github_branch,
                path_prefix=github_path_prefix,
                api_base_url=github_api_base_url,
                batch_window_seconds=github_batch_window_seconds,
            ),
        )
    elif storage_provider == STORAGE_PROVIDER_ONEDRIVE:
        onedrive = storage["onedrive"]
        onedrive_tenant_id = str(onedrive.get("tenant_id", "common")).strip()
        if not onedrive_tenant_id:
            onedrive_tenant_id = "common"

        raw_client_id = str(onedrive.get("client_id") or "").strip()
        onedrive_client_id = raw_client_id or None
        raw_client_secret = str(onedrive.get("client_secret") or "").strip()
        onedrive_client_secret = raw_client_secret or None
        onedrive_root_path = normalize_onedrive_root_path(
            onedrive.get("root_path", "Apps/telejournal")
        )
        onedrive_api_base_url = (
            str(
                onedrive.get(
                    "api_base_url",
                    "https://graph.microsoft.com/v1.0",
                )
            ).strip()
            or "https://graph.microsoft.com/v1.0"
        )
        onedrive_batch_window_seconds = int(onedrive.get("batch_window_seconds", 60))

        raw_access_token = str(onedrive.get("access_token", "")).strip()
        onedrive_access_token = raw_access_token or None
        raw_refresh_token = str(onedrive.get("refresh_token", "")).strip()
        onedrive_refresh_token = raw_refresh_token or None
        raw_expires_at = str(onedrive.get("token_expires_at_utc", "")).strip()
        onedrive_token_expires_at_utc = raw_expires_at or None

        if onedrive_client_id is None:
            raise ValueError(
                "storage.onedrive.client_id is required for onedrive provider"
            )
        if onedrive_client_secret is None:
            raise ValueError(
                "storage.onedrive.client_secret is required for onedrive provider"
            )
        if not onedrive_root_path:
            raise ValueError(
                "storage.onedrive.root_path is required for onedrive provider"
            )
        if onedrive_batch_window_seconds < 1:
            raise ValueError("storage.onedrive.batch_window_seconds must be >= 1")

        if onedrive_token_expires_at_utc is not None:
            try:
                datetime.strptime(onedrive_token_expires_at_utc, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as exc:
                raise ValueError(
                    "storage.onedrive.token_expires_at_utc must be ISO UTC "
                    "format YYYY-MM-DDTHH:MM:SSZ"
                ) from exc

        storage_settings = StorageSettings(
            provider=storage_provider,
            onedrive=OneDriveConfig(
                tenant_id=onedrive_tenant_id,
                client_id=onedrive_client_id,
                client_secret=onedrive_client_secret,
                root_path=onedrive_root_path,
                api_base_url=onedrive_api_base_url,
                batch_window_seconds=onedrive_batch_window_seconds,
                access_token=onedrive_access_token,
                refresh_token=onedrive_refresh_token,
                token_expires_at_utc=onedrive_token_expires_at_utc,
            ),
        )
    else:
        google_drive = storage["google_drive"]
        raw_client_id = str(google_drive.get("client_id") or "").strip()
        google_drive_client_id = raw_client_id or None
        raw_client_secret = str(google_drive.get("client_secret") or "").strip()
        google_drive_client_secret = raw_client_secret or None
        raw_folder_id = str(google_drive.get("folder_id") or "").strip()
        google_drive_folder_id = raw_folder_id or None
        google_drive_batch_window_seconds = int(
            google_drive.get("batch_window_seconds", 60)
        )

        raw_access_token = str(google_drive.get("access_token", "")).strip()
        google_drive_access_token = raw_access_token or None
        raw_refresh_token = str(google_drive.get("refresh_token", "")).strip()
        google_drive_refresh_token = raw_refresh_token or None
        raw_expires_at = str(google_drive.get("token_expires_at_utc", "")).strip()
        google_drive_token_expires_at_utc = raw_expires_at or None

        if google_drive_client_id is None:
            raise ValueError(
                "storage.google_drive.client_id is required for google_drive provider"
            )
        if google_drive_client_secret is None:
            raise ValueError(
                "storage.google_drive.client_secret is required for google_drive provider"
            )
        if google_drive_batch_window_seconds < 1:
            raise ValueError("storage.google_drive.batch_window_seconds must be >= 1")

        if google_drive_token_expires_at_utc is not None:
            try:
                datetime.strptime(
                    google_drive_token_expires_at_utc,
                    "%Y-%m-%dT%H:%M:%SZ",
                )
            except ValueError as exc:
                raise ValueError(
                    "storage.google_drive.token_expires_at_utc must be ISO UTC "
                    "format YYYY-MM-DDTHH:MM:SSZ"
                ) from exc

        storage_settings = StorageSettings(
            provider=storage_provider,
            google_drive=GoogleDriveConfig(
                client_id=google_drive_client_id,
                client_secret=google_drive_client_secret,
                folder_id=google_drive_folder_id,
                batch_window_seconds=google_drive_batch_window_seconds,
                access_token=google_drive_access_token,
                refresh_token=google_drive_refresh_token,
                token_expires_at_utc=google_drive_token_expires_at_utc,
            ),
        )

    log_level = str(merged.get("log_level", "INFO")).strip().upper() or "INFO"
    window_seconds = int(merged.get("message_timestamp_window_seconds", 60))
    if window_seconds < 0:
        raise ValueError("MESSAGE_TIMESTAMP_WINDOW_SECONDS must be >= 0")

    daily_brief_time_utc = parse_daily_brief_time_utc(
        merged.get("daily_brief_time_utc", "0")
    )
    tag_choices = parse_tag_choices(merged.get("tag_choices", []))
    prompt_for_mood_if_missing = parse_bool(
        merged.get("prompt_for_mood_if_missing", True)
    )
    bot_menu_enabled = parse_bool(merged.get("bot_menu_enabled", True))

    return Settings(
        telegram_token=token,
        vault_root=vault_root,
        allowed_user_ids=allowed_user_ids,
        log_level=log_level,
        message_timestamp_window_seconds=window_seconds,
        secure_file_permissions=secure_permissions,
        daily_brief_time_utc=daily_brief_time_utc,
        tag_choices=tag_choices,
        prompt_for_mood_if_missing=prompt_for_mood_if_missing,
        bot_menu_enabled=bot_menu_enabled,
        config_path=yaml_path,
        storage_provider=storage_provider,
        github_owner=github_owner,
        github_repo=github_repo,
        github_branch=github_branch,
        github_token=github_token,
        github_path_prefix=github_path_prefix,
        github_api_base_url=github_api_base_url,
        github_batch_window_seconds=github_batch_window_seconds,
        onedrive_tenant_id=onedrive_tenant_id,
        onedrive_client_id=onedrive_client_id,
        onedrive_client_secret=onedrive_client_secret,
        onedrive_root_path=onedrive_root_path,
        onedrive_api_base_url=onedrive_api_base_url,
        onedrive_batch_window_seconds=onedrive_batch_window_seconds,
        onedrive_access_token=onedrive_access_token,
        onedrive_refresh_token=onedrive_refresh_token,
        onedrive_token_expires_at_utc=onedrive_token_expires_at_utc,
        google_drive_client_id=google_drive_client_id,
        google_drive_client_secret=google_drive_client_secret,
        google_drive_folder_id=google_drive_folder_id,
        google_drive_batch_window_seconds=google_drive_batch_window_seconds,
        google_drive_access_token=google_drive_access_token,
        google_drive_refresh_token=google_drive_refresh_token,
        google_drive_token_expires_at_utc=google_drive_token_expires_at_utc,
        storage=storage_settings,
    )
