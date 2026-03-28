"""Configuration source loaders for Telejournal."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import cast

import yaml


def expand_env_vars(data: Any) -> Any:
    """Recursively expand environment variables in config values."""
    if isinstance(data, str):
        return os.path.expandvars(data)
    if isinstance(data, dict):
        return {key: expand_env_vars(value) for key, value in data.items()}
    if isinstance(data, list):
        return [expand_env_vars(value) for value in data]
    return data


def load_yaml_config(config_path: Path | None) -> dict[str, Any]:
    """Load and expand a YAML configuration file when provided."""
    if config_path is None:
        return {}

    with config_path.open("r", encoding="utf-8") as file_handle:
        raw_config = yaml.safe_load(file_handle)

    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise ValueError("YAML configuration root must be a mapping")

    expanded = expand_env_vars(raw_config)
    return cast(dict[str, Any], expanded)


def load_env_config() -> dict[str, Any]:
    """Load settings-like values from environment variables."""
    config: dict[str, Any] = {}

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if token:
        config["telegram_token"] = token

    allowed_user_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if allowed_user_ids:
        config["allowed_user_ids"] = allowed_user_ids

    log_level = os.getenv("LOG_LEVEL", "").strip()
    if log_level:
        config["log_level"] = log_level

    window_seconds = os.getenv("MESSAGE_TIMESTAMP_WINDOW_SECONDS", "").strip()
    if window_seconds:
        config["message_timestamp_window_seconds"] = window_seconds

    daily_brief_time_utc = os.getenv("DAILY_BRIEF_TIME_UTC", "").strip()
    if daily_brief_time_utc:
        config["daily_brief_time_utc"] = daily_brief_time_utc

    tag_choices = os.getenv("TAG_CHOICES", "").strip()
    if tag_choices:
        config["tag_choices"] = tag_choices

    prompt_for_mood_if_missing = os.getenv("PROMPT_FOR_MOOD_IF_MISSING", "").strip()
    if prompt_for_mood_if_missing:
        config["prompt_for_mood_if_missing"] = prompt_for_mood_if_missing

    bot_menu_enabled = os.getenv("BOT_MENU_ENABLED", "").strip()
    if bot_menu_enabled:
        config["bot_menu_enabled"] = bot_menu_enabled

    storage_provider = os.getenv("STORAGE_PROVIDER", "").strip()
    obsidian_root = os.getenv("STORAGE_OBSIDIAN_VAULT_ROOT", "").strip()
    obsidian_secure = os.getenv(
        "STORAGE_OBSIDIAN_VAULT_SECURE_FILE_PERMISSIONS", ""
    ).strip()
    github_owner = os.getenv("STORAGE_GITHUB_OWNER", "").strip()
    github_repo = os.getenv("STORAGE_GITHUB_REPO", "").strip()
    github_branch = os.getenv("STORAGE_GITHUB_BRANCH", "").strip()
    github_token = os.getenv("STORAGE_GITHUB_TOKEN", "").strip()
    github_path_prefix = os.getenv("STORAGE_GITHUB_PATH_PREFIX", "").strip()
    github_api_base_url = os.getenv("STORAGE_GITHUB_API_BASE_URL", "").strip()
    github_batch_window_seconds = os.getenv(
        "STORAGE_GITHUB_BATCH_WINDOW_SECONDS", ""
    ).strip()
    onedrive_tenant_id = os.getenv("STORAGE_ONEDRIVE_TENANT_ID", "").strip()
    onedrive_client_id = os.getenv("STORAGE_ONEDRIVE_CLIENT_ID", "").strip()
    onedrive_client_secret = os.getenv("STORAGE_ONEDRIVE_CLIENT_SECRET", "").strip()
    onedrive_root_path = os.getenv("STORAGE_ONEDRIVE_ROOT_PATH", "").strip()
    onedrive_api_base_url = os.getenv("STORAGE_ONEDRIVE_API_BASE_URL", "").strip()
    onedrive_batch_window_seconds = os.getenv(
        "STORAGE_ONEDRIVE_BATCH_WINDOW_SECONDS", ""
    ).strip()
    onedrive_access_token = os.getenv("STORAGE_ONEDRIVE_ACCESS_TOKEN", "").strip()
    onedrive_refresh_token = os.getenv("STORAGE_ONEDRIVE_REFRESH_TOKEN", "").strip()
    onedrive_token_expires_at_utc = os.getenv(
        "STORAGE_ONEDRIVE_TOKEN_EXPIRES_AT_UTC", ""
    ).strip()

    has_storage = any(
        (
            storage_provider,
            obsidian_root,
            obsidian_secure,
            github_owner,
            github_repo,
            github_branch,
            github_token,
            github_path_prefix,
            github_api_base_url,
            github_batch_window_seconds,
            onedrive_tenant_id,
            onedrive_client_id,
            onedrive_client_secret,
            onedrive_root_path,
            onedrive_api_base_url,
            onedrive_batch_window_seconds,
            onedrive_access_token,
            onedrive_refresh_token,
            onedrive_token_expires_at_utc,
        )
    )
    if has_storage:
        storage: dict[str, Any] = {
            "provider": storage_provider or None,
            "obsidian_vault": {
                "root": obsidian_root or None,
                "secure_file_permissions": obsidian_secure or None,
            },
            "github_repo": {
                "owner": github_owner or None,
                "repo": github_repo or None,
                "branch": github_branch or None,
                "token": github_token or None,
                "path_prefix": github_path_prefix or None,
                "api_base_url": github_api_base_url or None,
                "batch_window_seconds": github_batch_window_seconds or None,
            },
            "onedrive": {
                "tenant_id": onedrive_tenant_id or None,
                "client_id": onedrive_client_id or None,
                "client_secret": onedrive_client_secret or None,
                "root_path": onedrive_root_path or None,
                "api_base_url": onedrive_api_base_url or None,
                "batch_window_seconds": onedrive_batch_window_seconds or None,
                "access_token": onedrive_access_token or None,
                "refresh_token": onedrive_refresh_token or None,
                "token_expires_at_utc": onedrive_token_expires_at_utc or None,
            },
        }
        config["storage"] = storage

    expanded = expand_env_vars(config)
    return cast(dict[str, Any], expanded)
