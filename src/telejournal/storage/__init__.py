"""Storage providers and repository factory."""

from .common import (
    FlushEvent,
    FlushEventPublisher,
    NoteData,
    PendingWrite,
    ProviderCapabilities,
    StorageAuthorizationRequiredError,
    SupportsFlushEventSubscription,
    SupportsProviderCapabilities,
    WriteVisibility,
)
from .factory import build_repository
from .github import GitHubRepository
from .google_drive import (
    GoogleDriveAuthorizationRequiredError,
    GoogleDriveRepository,
)
from .obsidian import VaultRepository
from .onedrive import OneDriveAuthorizationRequiredError, OneDriveRepository

__all__ = [
    "build_repository",
    "GitHubRepository",
    "GoogleDriveRepository",
    "GoogleDriveAuthorizationRequiredError",
    "NoteData",
    "FlushEvent",
    "FlushEventPublisher",
    "OneDriveAuthorizationRequiredError",
    "OneDriveRepository",
    "PendingWrite",
    "ProviderCapabilities",
    "SupportsFlushEventSubscription",
    "SupportsProviderCapabilities",
    "StorageAuthorizationRequiredError",
    "VaultRepository",
    "WriteVisibility",
]
