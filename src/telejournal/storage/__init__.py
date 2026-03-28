"""Storage providers and repository factory."""

from .common import (
    NoteData,
    PendingWrite,
    StorageAuthorizationRequiredError,
)
from .factory import build_repository
from .github import GitHubRepository
from .obsidian import VaultRepository
from .onedrive import OneDriveAuthorizationRequiredError, OneDriveRepository

__all__ = [
    "build_repository",
    "GitHubRepository",
    "NoteData",
    "OneDriveAuthorizationRequiredError",
    "OneDriveRepository",
    "PendingWrite",
    "StorageAuthorizationRequiredError",
    "VaultRepository",
]
