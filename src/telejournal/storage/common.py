"""Shared types and constants for storage providers."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)
_TIMESTAMP_RE = re.compile(
    r"^\s*(?:%%\s*)?(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?:\s*%%)?"
)


@dataclass
class NoteData:
    """In-memory representation of a note with frontmatter and body."""

    frontmatter: dict[str, Any]
    body: str


@dataclass
class PendingWrite:
    """Queued provider content write that will be flushed in batch."""

    payload: bytes
    message: str


class WriteVisibility(StrEnum):
    """Describe when writes become externally visible to the user."""

    IMMEDIATE = "immediate"
    BUFFERED = "buffered"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declare behavioral capabilities for one storage provider."""

    write_visibility: WriteVisibility


class SupportsProviderCapabilities(Protocol):
    """Protocol for providers exposing explicit storage capabilities."""

    capabilities: ProviderCapabilities


class SupportsFlushEventSubscription(Protocol):
    """Protocol for providers that support flush-event listeners."""

    def add_flush_listener(self, listener: Callable[[FlushEvent], None]) -> None:
        """Register one callback invoked on successful queue flushes."""


@dataclass(frozen=True)
class FlushEvent:
    """Successful queued-flush event emitted by buffered providers."""

    provider: str
    flush_cycle: int
    upserts: int
    deletes: int
    reason: str


class FlushEventPublisher:
    """Provide event subscription for providers that flush queued writes."""

    def __init__(self) -> None:
        """Initialize listener collection used for flush notifications."""
        self._flush_listeners: list[Callable[[FlushEvent], None]] = []

    def add_flush_listener(self, listener: Callable[[FlushEvent], None]) -> None:
        """Register one callback invoked on successful queue flushes."""
        if listener in self._flush_listeners:
            return
        self._flush_listeners.append(listener)

    def _emit_flush_event(self, event: FlushEvent) -> None:
        """Notify listeners about a successful batch flush."""
        for listener in list(self._flush_listeners):
            try:
                listener(event)
            except Exception:
                LOGGER.exception("Storage flush listener raised unexpectedly")


class StorageAuthorizationRequiredError(RuntimeError):
    """Raised when a storage backend requires interactive authorization."""
