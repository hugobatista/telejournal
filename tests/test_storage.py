"""Unit tests for vault storage behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from telegram_journal_bot.storage import VaultRepository


@pytest.mark.asyncio
async def test_append_entry_creates_note_with_defaults(tmp_path: Path) -> None:
    """Appending first entry should initialize frontmatter and body."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    note_path = await repo.append_entry(note_dt, "18:34 - First entry #journal")
    content = note_path.read_text(encoding="utf-8")

    assert "mood: null" in content
    assert "tags:" in content
    assert "- journal" in content
    assert "18:34 - First entry #journal" in content


@pytest.mark.asyncio
async def test_frontmatter_updates_preserve_body(tmp_path: Path) -> None:
    """Frontmatter update should not remove note content."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(note_dt, "18:34 - Body")
    note_path = await repo.update_frontmatter(
        note_dt,
        {"mood": 4, "tags": ["journal", "work"]},
    )

    content = note_path.read_text(encoding="utf-8")
    assert "mood: 4" in content
    assert "- work" in content
    assert "18:34 - Body" in content


@pytest.mark.asyncio
async def test_get_last_entry_time_reads_latest_timestamp(tmp_path: Path) -> None:
    """Last entry timestamp should be inferred from latest HH:MM line."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(note_dt, "18:34 - One")
    await repo.append_entry(note_dt, "19:15 - Two")

    last = await repo.get_last_entry_time(note_dt)
    assert last == datetime(2026, 3, 7, 19, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_note_path_and_frontmatter_helpers(tmp_path: Path) -> None:
    """Repository helpers should create paths and default frontmatter."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 1, 2, 3, tzinfo=UTC)

    note_path = repo.get_note_path(note_dt)
    assert note_path.parent.exists()
    assert repo.vault_root == tmp_path

    fm = await repo.get_note_frontmatter(note_dt)
    assert fm["mood"] is None
    assert fm["tags"] == ["journal"]


@pytest.mark.asyncio
async def test_note_presence_and_mood_checks(tmp_path: Path) -> None:
    """Presence and mood helpers should track note state changes."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    assert not await repo.note_has_entry(note_dt)
    assert not await repo.note_has_mood(note_dt)

    await repo.append_entry(note_dt, "18:34 - hi", {"mood": 3})
    assert await repo.note_has_entry(note_dt)
    assert await repo.note_has_mood(note_dt)


@pytest.mark.asyncio
async def test_split_frontmatter_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed frontmatter should fall back safely."""
    repo = VaultRepository(tmp_path)

    empty_fm, raw_body = repo._split_frontmatter("no frontmatter")
    assert empty_fm == {}
    assert raw_body == "no frontmatter"

    empty_fm, raw_body = repo._split_frontmatter("---\na: 1\n")
    assert empty_fm == {}
    assert raw_body.startswith("---")

    non_mapping = "---\n- one\n- two\n---\n\nbody"
    empty_fm, body = repo._split_frontmatter(non_mapping)
    assert empty_fm == {}
    assert body == "body"

    def _raise(_: str) -> Any:
        raise yaml.YAMLError("bad")

    monkeypatch.setattr(yaml, "safe_load", _raise)
    empty_fm, body = repo._split_frontmatter("---\na: 1\n---\n\nbody")
    assert empty_fm == {}
    assert body == "body"


@pytest.mark.asyncio
async def test_last_entry_time_none_and_no_timestamp(tmp_path: Path) -> None:
    """Timestamp parser should return None when body has no HH:MM lines."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    assert await repo.get_last_entry_time(note_dt) is None

    await repo.append_entry(note_dt, "not a timestamp")
    assert await repo.get_last_entry_time(note_dt) is None


@pytest.mark.asyncio
async def test_save_photo_collision_suffix(tmp_path: Path) -> None:
    """Photo writer should suffix duplicate filenames."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    year = tmp_path / "2026" / "attachments"
    year.mkdir(parents=True, exist_ok=True)
    (year / "20260307_183442.jpg").write_text("x", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock())
    photo = SimpleNamespace(get_file=AsyncMock(return_value=downloader))

    rel_path = await repo.save_photo(photo, note_dt, "20260307_183442")  # type: ignore[arg-type]
    assert rel_path.endswith("20260307_183442_1.jpg")


@pytest.mark.asyncio
async def test_append_entry_with_frontmatter_updates(tmp_path: Path) -> None:
    """Append should merge frontmatter updates into note output."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    note_path = await repo.append_entry(
        note_dt,
        "18:34 - merged",
        {"tags": ["journal", "work"], "mood": 4},
    )
    text = note_path.read_text(encoding="utf-8")
    assert "mood: 4" in text
    assert "- work" in text
