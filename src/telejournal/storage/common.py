"""Shared types and constants for storage providers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

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


class StorageAuthorizationRequiredError(RuntimeError):
    """Raised when a storage backend requires interactive authorization."""
