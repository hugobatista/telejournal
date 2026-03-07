"""Vault read/write operations for daily notes and attachments."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles
import yaml
from telegram import PhotoSize

LOGGER = logging.getLogger(__name__)
_TIMESTAMP_RE = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2})")


@dataclass
class NoteData:
    """In-memory representation of a note with frontmatter and body."""

    frontmatter: dict[str, Any]
    body: str


class VaultRepository:
    """Manage journal note and attachment persistence in the vault."""

    def __init__(self, vault_root: Path) -> None:
        """Initialize a repository rooted at an Obsidian vault path."""
        self._vault_root = vault_root
        self._locks: dict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def vault_root(self) -> Path:
        """Expose the configured vault root directory."""
        return self._vault_root

    def get_note_path(self, note_dt: datetime) -> Path:
        """Return note path `YYYY/YYYY-MM-DD.md` and create year dir."""
        year_dir = self._vault_root / str(note_dt.year)
        year_dir.mkdir(parents=True, exist_ok=True)
        return year_dir / f"{note_dt.strftime('%Y-%m-%d')}.md"

    def _get_attachments_dir(self, note_dt: datetime) -> Path:
        """Return attachment directory path and ensure it exists."""
        year_dir = self._vault_root / str(note_dt.year)
        attachments_dir = year_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        return attachments_dir

    def _default_frontmatter(self, note_dt: datetime) -> dict[str, Any]:
        """Create default YAML frontmatter for a date."""
        start_of_day = datetime.combine(
            note_dt.date(),
            datetime.min.time(),
        ).replace(tzinfo=UTC)

        return {
            "mood": None,
            "tags": ["journal"],
            "created": start_of_day.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _split_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Extract YAML frontmatter and markdown body from note content."""
        if not content.startswith("---\n"):
            return {}, content

        closing_index = content.find("\n---\n", 4)
        if closing_index == -1:
            return {}, content

        raw_yaml = content[4:closing_index]
        body = content[closing_index + 5 :].lstrip("\n")

        try:
            loaded = yaml.safe_load(raw_yaml) or {}
            if not isinstance(loaded, dict):
                raise ValueError("frontmatter is not a mapping")
            return loaded, body
        except Exception:  # pragma: no cover
            LOGGER.exception("YAML parse error, resetting frontmatter")
            return {}, body

    def _serialize_note(self, frontmatter: dict[str, Any], body: str) -> str:
        """Serialize frontmatter and body into markdown file content."""
        rendered_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        rendered_body = body.rstrip()
        if rendered_body:
            rendered_body = f"{rendered_body}\n"

        return f"---\n{rendered_yaml}\n---\n\n{rendered_body}"

    async def _read_note(self, note_path: Path) -> NoteData:
        """Read note content and return parsed frontmatter/body."""
        if not note_path.exists():
            return NoteData(frontmatter={}, body="")

        async with aiofiles.open(note_path, "r", encoding="utf-8") as handle:
            content = await handle.read()

        frontmatter, body = self._split_frontmatter(content)
        return NoteData(frontmatter=frontmatter, body=body)

    async def _write_note(
        self,
        note_path: Path,
        frontmatter: dict[str, Any],
        body: str,
    ) -> None:
        """Atomically write note content to disk."""
        tmp_path = note_path.with_suffix(".md.tmp")
        rendered = self._serialize_note(frontmatter, body)

        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as handle:
            await handle.write(rendered)

        await asyncio.to_thread(os.replace, tmp_path, note_path)

    async def get_note_frontmatter(self, note_dt: datetime) -> dict[str, Any]:
        """Load frontmatter for a note and apply defaults."""
        note_path = self.get_note_path(note_dt)
        note_data = await self._read_note(note_path)
        defaults = self._default_frontmatter(note_dt)
        defaults.update(note_data.frontmatter)
        return defaults

    async def append_entry(
        self,
        note_dt: datetime,
        entry: str,
        frontmatter_updates: dict[str, Any] | None = None,
    ) -> Path:
        """Append a journal entry to a daily note and update frontmatter."""
        note_path = self.get_note_path(note_dt)
        lock = self._locks[note_path]
        async with lock:
            note_data = await self._read_note(note_path)
            frontmatter = self._default_frontmatter(note_dt)
            frontmatter.update(note_data.frontmatter)

            if frontmatter_updates:
                frontmatter.update(frontmatter_updates)

            body_parts = [
                part for part in [note_data.body.rstrip(), entry.strip()] if part
            ]
            next_body = "\n\n".join(body_parts)

            await self._write_note(note_path, frontmatter, next_body)

        return note_path

    async def update_frontmatter(
        self,
        note_dt: datetime,
        updates: dict[str, Any],
    ) -> Path:
        """Update only frontmatter values while preserving note body."""
        note_path = self.get_note_path(note_dt)
        lock = self._locks[note_path]
        async with lock:
            note_data = await self._read_note(note_path)
            frontmatter = self._default_frontmatter(note_dt)
            frontmatter.update(note_data.frontmatter)
            frontmatter.update(updates)
            await self._write_note(note_path, frontmatter, note_data.body)
        return note_path

    async def note_has_entry(self, note_dt: datetime) -> bool:
        """Return whether a note contains at least one body entry."""
        note_path = self.get_note_path(note_dt)
        note_data = await self._read_note(note_path)
        return bool(note_data.body.strip())

    async def note_has_mood(self, note_dt: datetime) -> bool:
        """Return whether a note has mood set to a non-null value."""
        frontmatter = await self.get_note_frontmatter(note_dt)
        mood = frontmatter.get("mood")
        return mood is not None

    async def get_last_entry_time(
        self,
        note_dt: datetime,
    ) -> datetime | None:
        """Infer the last entry timestamp from note body HH:MM lines."""
        note_path = self.get_note_path(note_dt)
        note_data = await self._read_note(note_path)
        if not note_data.body.strip():
            return None

        last_time: datetime | None = None
        for line in note_data.body.splitlines():
            match = _TIMESTAMP_RE.match(line.strip())
            if not match:
                continue

            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            parsed = datetime.combine(
                note_dt.date(),
                datetime.min.time().replace(hour=hour, minute=minute),
            ).replace(tzinfo=UTC)
            last_time = parsed

        return last_time

    async def save_photo(
        self,
        photo: PhotoSize,
        note_dt: datetime,
        ts: str,
    ) -> str:
        """Download a photo and return Obsidian embed path."""
        attachments_dir = self._get_attachments_dir(note_dt)
        filename = f"{ts}.jpg"
        output_path = attachments_dir / filename

        counter = 1
        while output_path.exists():
            filename = f"{ts}_{counter}.jpg"
            output_path = attachments_dir / filename
            counter += 1

        tg_file = await photo.get_file()
        await tg_file.download_to_drive(output_path)
        return f"{note_dt.year}/attachments/{filename}"
