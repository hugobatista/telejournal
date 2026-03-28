"""Configuration loading for the Telegram journal bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
import re
from typing import Any

from telejournal.config_loader import expand_env_vars, load_env_config, load_yaml_config

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


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved from environment variables."""

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

_TAG_CHOICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _parse_allowed_user_ids(raw_value: str) -> set[int]:
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


def _parse_bool(raw_value: str | bool) -> bool:
    """Parse bool-like values from strings or booleans."""
    if isinstance(raw_value, bool):
        return raw_value
    return raw_value.strip().lower() in ("true", "1", "yes", "on")


def _normalize_allowed_user_ids(raw_value: Any) -> set[int]:
    """Normalize allowed user IDs from string/list/set values."""
    if isinstance(raw_value, str):
        return _parse_allowed_user_ids(raw_value)
    if isinstance(raw_value, (list, set, tuple)):
        parsed = {int(value) for value in raw_value}
        if not parsed:
            raise ValueError(
                "TELEGRAM_ALLOWED_USER_IDS must contain at least one valid user ID"
            )
        return parsed
    raise ValueError("TELEGRAM_ALLOWED_USER_IDS must be a CSV string or list")


def _parse_daily_brief_time_utc(raw_value: Any) -> time | None:
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


def _parse_tag_choices(raw_value: Any) -> tuple[str, ...]:
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


def _resolve_config_path(config_path: Path | None) -> Path | None:
    """Resolve config path to an absolute path when provided."""
    if config_path is None:
        return None
    return config_path.expanduser().resolve()


def _merge_values(base: Any, override: Any) -> Any:
    """Recursively merge config values while ignoring ``None`` overrides."""
    if override is None:
        return base
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _merge_values(merged[key], value)
            elif value is not None:
                merged[key] = value
        return merged
    return override


def _merge_configs(*sources: dict[str, Any]) -> dict[str, Any]:
    """Merge config dictionaries from lowest to highest priority."""
    merged: dict[str, Any] = {}
    for source in sources:
        merged = _merge_values(merged, source)
    return merged


def _storage_node(merged: dict[str, Any]) -> dict[str, Any]:
    """Return normalized storage mapping with expected nested keys."""
    raw_storage = merged.get("storage")
    storage = raw_storage if isinstance(raw_storage, dict) else {}

    raw_obsidian = storage.get("obsidian_vault")
    obsidian = raw_obsidian if isinstance(raw_obsidian, dict) else {}

    raw_github = storage.get("github_repo")
    github = raw_github if isinstance(raw_github, dict) else {}

    raw_onedrive = storage.get("onedrive")
    onedrive = raw_onedrive if isinstance(raw_onedrive, dict) else {}

    return {
        "provider": storage.get("provider"),
        "obsidian_vault": obsidian,
        "github_repo": github,
        "onedrive": onedrive,
    }


def _normalize_onedrive_root_path(raw_value: Any) -> str:
    """Normalize OneDrive root path to a clean slash-separated segment."""
    value = str(raw_value or "").strip().replace("\\", "/")
    value = value.strip("/")
    return value


def _normalize_path_prefix(raw_value: Any) -> str:
    """Normalize optional GitHub path prefix to a clean relative segment."""
    value = str(raw_value or "").strip().replace("\\", "/")
    value = value.strip("/")
    if value in ("", "."):
        return ""
    return value


def load_settings(
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Settings:
    """Load settings from defaults, env, YAML, and CLI with priority order."""
    yaml_path = _resolve_config_path(config_path)
    yaml_config = load_yaml_config(yaml_path)
    env_config = load_env_config()
    cli_config = expand_env_vars(cli_overrides or {})

    merged = _merge_configs(DEFAULT_SETTINGS, env_config, yaml_config, cli_config)

    token = str(merged.get("telegram_token", "")).strip()
    if not token:
        raise ValueError("TELEGRAM_TOKEN is required")

    allowed_raw = merged.get("allowed_user_ids", "")
    if not allowed_raw:
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS is required")
    allowed_user_ids = _normalize_allowed_user_ids(allowed_raw)

    storage = _storage_node(merged)
    storage_provider = str(storage.get("provider") or "").strip().lower()
    if storage_provider not in (
        STORAGE_PROVIDER_OBSIDIAN,
        STORAGE_PROVIDER_GITHUB,
        STORAGE_PROVIDER_ONEDRIVE,
    ):
        raise ValueError(
            "storage.provider must be 'obsidian_vault', 'github_repo', or 'onedrive'"
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

    if storage_provider == STORAGE_PROVIDER_OBSIDIAN:
        obsidian = storage["obsidian_vault"]
        root_raw = str(obsidian.get("root", "")).strip()
        if not root_raw:
            raise ValueError(
                "storage.obsidian_vault.root is required for obsidian_vault provider"
            )
        vault_root = Path(root_raw).expanduser().resolve()
        vault_root.mkdir(parents=True, exist_ok=True)
        secure_permissions = _parse_bool(obsidian.get("secure_file_permissions", True))
    elif storage_provider == STORAGE_PROVIDER_GITHUB:
        github = storage["github_repo"]
        github_owner = str(github.get("owner", "")).strip()
        github_repo = str(github.get("repo", "")).strip()
        github_token = str(github.get("token", "")).strip()
        github_branch = str(github.get("branch", "main")).strip() or "main"
        github_path_prefix = _normalize_path_prefix(github.get("path_prefix", ""))
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
    else:
        onedrive = storage["onedrive"]
        onedrive_tenant_id = str(onedrive.get("tenant_id", "common")).strip()
        if not onedrive_tenant_id:
            onedrive_tenant_id = "common"

        raw_client_id = str(onedrive.get("client_id") or "").strip()
        onedrive_client_id = raw_client_id or None
        raw_client_secret = str(onedrive.get("client_secret") or "").strip()
        onedrive_client_secret = raw_client_secret or None
        onedrive_root_path = _normalize_onedrive_root_path(
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

    log_level = str(merged.get("log_level", "INFO")).strip().upper() or "INFO"
    window_seconds = int(merged.get("message_timestamp_window_seconds", 60))
    if window_seconds < 0:
        raise ValueError("MESSAGE_TIMESTAMP_WINDOW_SECONDS must be >= 0")

    daily_brief_time_utc = _parse_daily_brief_time_utc(
        merged.get("daily_brief_time_utc", "0")
    )
    tag_choices = _parse_tag_choices(merged.get("tag_choices", []))
    prompt_for_mood_if_missing = _parse_bool(
        merged.get("prompt_for_mood_if_missing", True)
    )
    bot_menu_enabled = _parse_bool(merged.get("bot_menu_enabled", True))

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
    )
