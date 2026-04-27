"""Configuration merge utilities."""

from __future__ import annotations

from typing import Any


def merge_values(base: Any, override: Any) -> Any:
    """Recursively merge config values while ignoring ``None`` overrides."""
    if override is None:
        return base
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = merge_values(merged[key], value)
            elif value is not None:
                merged[key] = value
        return merged
    return override


def merge_configs(*sources: dict[str, Any]) -> dict[str, Any]:
    """Merge config dictionaries from lowest to highest priority."""
    merged: dict[str, Any] = {}
    for source in sources:
        merged = merge_values(merged, source)
    return merged


def storage_node(merged: dict[str, Any]) -> dict[str, Any]:
    """Return normalized storage mapping with expected nested keys."""
    raw_storage = merged.get("storage")
    storage = raw_storage if isinstance(raw_storage, dict) else {}

    raw_obsidian = storage.get("obsidian_vault")
    obsidian = raw_obsidian if isinstance(raw_obsidian, dict) else {}

    raw_github = storage.get("github_repo")
    github = raw_github if isinstance(raw_github, dict) else {}

    raw_onedrive = storage.get("onedrive")
    onedrive = raw_onedrive if isinstance(raw_onedrive, dict) else {}

    raw_google_drive = storage.get("google_drive")
    google_drive = raw_google_drive if isinstance(raw_google_drive, dict) else {}

    return {
        "provider": storage.get("provider"),
        "obsidian_vault": obsidian,
        "github_repo": github,
        "onedrive": onedrive,
        "google_drive": google_drive,
    }
