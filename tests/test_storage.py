"""Unit tests for vault storage behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from telejournal.formatting import marker_end_comment, marker_start_comment
from telejournal.storage import VaultRepository


@pytest.mark.asyncio
async def test_append_entry_creates_note_with_defaults(tmp_path: Path) -> None:
    """Appending first entry should initialize frontmatter and body."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    note_path = await repo.append_entry(
        note_dt,
        "%% 18:34:42 %%\nFirst entry #journal",
    )
    content = note_path.read_text(encoding="utf-8")

    assert "mood: null" in content
    assert "location: null" in content
    assert "tags:" in content
    assert "- journal" in content
    assert "%% 18:34:42 %%\nFirst entry #journal" in content


@pytest.mark.asyncio
async def test_append_entry_creates_note_with_today_defaults(tmp_path: Path) -> None:
    """Appending entry for today should set 'created' to current datetime."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    # Mock datetime.now to return a datetime on the same date as note_dt
    with patch("telejournal.storage.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 7, 12, 0, 0, tzinfo=UTC)
        mock_dt.combine = datetime.combine
        mock_dt.min.time = datetime.min.time
        mock_dt.UTC = UTC

        note_path = await repo.append_entry(
            note_dt,
            "%% 18:34:42 %%\nToday's entry #journal",
        )
        content = note_path.read_text(encoding="utf-8")

        # Should have created set to the mocked now
        assert "created: '2026-03-07T12:00:00Z'" in content


@pytest.mark.asyncio
async def test_frontmatter_updates_preserve_body(tmp_path: Path) -> None:
    """Frontmatter update should not remove note content."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(note_dt, "%% 18:34:42 %%\nBody")
    note_path = await repo.update_frontmatter(
        note_dt,
        {"mood": 4, "tags": ["journal", "work"]},
    )

    content = note_path.read_text(encoding="utf-8")
    assert "mood: 4" in content
    assert "- work" in content
    assert "%% 18:34:42 %%\nBody" in content


@pytest.mark.asyncio
async def test_get_last_entry_time_reads_latest_timestamp(tmp_path: Path) -> None:
    """Last entry timestamp should be inferred from latest HH:MM line."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(note_dt, "%% 18:34:42 %%\nOne")
    await repo.append_entry(note_dt, "%% 19:15:07 %%\nTwo")

    last = await repo.get_last_entry_time(note_dt)
    assert last == datetime(2026, 3, 7, 19, 15, 7, tzinfo=UTC)


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
    assert fm["location"] is None
    assert fm["tags"] == ["journal"]


@pytest.mark.asyncio
async def test_note_presence_and_mood_checks(tmp_path: Path) -> None:
    """Presence and mood helpers should track note state changes."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    assert not await repo.note_has_entry(note_dt)
    assert not await repo.note_has_mood(note_dt)

    await repo.append_entry(
        note_dt,
        "%% 18:34:42 %%\nhi",
        {"mood": 3},
    )
    assert await repo.note_has_entry(note_dt)
    assert await repo.note_has_mood(note_dt)


@pytest.mark.asyncio
async def test_split_frontmatter_edge_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # Create mock that actually writes file to test chmod path
    async def mock_download(path: Path) -> None:
        path.write_text("photo_data", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock(side_effect=mock_download))
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
        "%% 18:34:42 %%\nmerged",
        {
            "tags": ["journal", "work"],
            "mood": 4,
        },
    )
    text = note_path.read_text(encoding="utf-8")
    assert "mood: 4" in text
    assert "- work" in text


@pytest.mark.asyncio
async def test_append_entry_continuation_adds_single_line_break(tmp_path: Path) -> None:
    """Continuation appends should extend the last entry block, not add bullets."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(note_dt, "%% 18:34:42 %%\nfirst")
    await repo.append_entry(note_dt, "second", as_continuation=True)

    content = repo.get_note_path(note_dt).read_text(encoding="utf-8")
    assert "%% 18:34:42 %%\nfirst\nsecond" in content
    assert "first\n\nsecond" not in content


@pytest.mark.asyncio
async def test_peek_last_entry(tmp_path: Path) -> None:
    """Peek helper should return last entry without removing it."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    assert await repo.peek_last_entry(note_dt) is None

    await repo.append_entry(note_dt, "%% 18:34:42 %%\none")
    await repo.append_entry(note_dt, "%% 18:35:00 %%\ntwo")

    peeked = await repo.peek_last_entry(note_dt)
    assert peeked == "%% 18:35:00 %%\ntwo"
    assert await repo.delete_last_entry(note_dt) == "%% 18:35:00 %%\ntwo"


@pytest.mark.asyncio
async def test_delete_last_entry_removes_tail_block(tmp_path: Path) -> None:
    """Delete helper should remove the last entry block from note body."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(note_dt, "%% 18:34:42 %%\none")
    await repo.append_entry(note_dt, "%% 18:35:00 %%\ntwo")

    removed = await repo.delete_last_entry(note_dt)
    assert removed == "%% 18:35:00 %%\ntwo"

    note_path = repo.get_note_path(note_dt)
    content = note_path.read_text(encoding="utf-8")
    assert "%% 18:34:42 %%\none" in content
    assert "%% 18:35:00 %%\ntwo" not in content


@pytest.mark.asyncio
async def test_delete_last_entry_none_when_empty(tmp_path: Path) -> None:
    """Delete helper should return None when note body is empty."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    assert await repo.delete_last_entry(note_dt) is None


@pytest.mark.asyncio
async def test_get_note_content_and_missing(tmp_path: Path) -> None:
    """Content helper should return text for existing note and None otherwise."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    assert await repo.get_note_content(note_dt) is None

    await repo.append_entry(note_dt, "%% 18:34:42 %%\nhello")
    content = await repo.get_note_content(note_dt)
    assert content is not None
    assert "hello" in content


@pytest.mark.asyncio
async def test_delete_day(tmp_path: Path) -> None:
    """Delete day should remove note file and report missing days as False."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    assert not await repo.delete_day(note_dt)
    await repo.append_entry(note_dt, "%% 18:34:42 %%\nhello")
    assert await repo.delete_day(note_dt)
    assert not repo.get_note_path(note_dt).exists()


@pytest.mark.asyncio
async def test_note_has_mood_handles_legacy_shapes(tmp_path: Path) -> None:
    """Mood detector should support list/dict legacy frontmatter shapes."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    await repo.append_entry(
        note_dt,
        "%% 18:34:42 %%\nlist mood",
        {"mood": [{"value": 2}]},
    )
    assert await repo.note_has_mood(note_dt)

    await repo.update_frontmatter(note_dt, {"mood": {"value": 4}})
    assert await repo.note_has_mood(note_dt)

    await repo.update_frontmatter(note_dt, {"mood": {"value": "x"}})
    assert not await repo.note_has_mood(note_dt)

    await repo.update_frontmatter(note_dt, {"mood": [{"value": "x"}]})
    assert not await repo.note_has_mood(note_dt)


@pytest.mark.asyncio
async def test_save_voice_collision_suffix(tmp_path: Path) -> None:
    """Voice writer should suffix duplicate filenames."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    year = tmp_path / "2026" / "attachments"
    year.mkdir(parents=True, exist_ok=True)
    (year / "20260307_183442.ogg").write_text("x", encoding="utf-8")

    # Create mock that actually writes file to test chmod path
    async def mock_download(path: Path) -> None:
        path.write_text("voice_data", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock(side_effect=mock_download))
    voice = SimpleNamespace(get_file=AsyncMock(return_value=downloader))

    rel_path = await repo.save_voice(voice, note_dt, "20260307_183442")  # type: ignore[arg-type]
    assert rel_path.endswith("20260307_183442_1.ogg")


@pytest.mark.asyncio
async def test_save_video_collision_suffix(tmp_path: Path) -> None:
    """Video writer should suffix duplicate filenames."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    year = tmp_path / "2026" / "attachments"
    year.mkdir(parents=True, exist_ok=True)
    (year / "20260307_183442.mp4").write_text("x", encoding="utf-8")

    # Create mock that actually writes file to test chmod path
    async def mock_download(path: Path) -> None:
        path.write_text("video_data", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock(side_effect=mock_download))
    video = SimpleNamespace(get_file=AsyncMock(return_value=downloader))

    rel_path = await repo.save_video(video, note_dt, "20260307_183442")  # type: ignore[arg-type]
    assert rel_path.endswith("20260307_183442_1.mp4")


@pytest.mark.asyncio
async def test_save_video_note_collision_suffix(tmp_path: Path) -> None:
    """Video note writer should suffix duplicate filenames."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    year = tmp_path / "2026" / "attachments"
    year.mkdir(parents=True, exist_ok=True)
    (year / "20260307_183442_note.mp4").write_text("x", encoding="utf-8")

    # Create mock that actually writes file to test chmod path
    async def mock_download(path: Path) -> None:
        path.write_text("video_note_data", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock(side_effect=mock_download))
    video_note = SimpleNamespace(get_file=AsyncMock(return_value=downloader))

    rel_path = await repo.save_video_note(video_note, note_dt, "20260307_183442")  # type: ignore[arg-type]
    assert rel_path.endswith("20260307_183442_note_1.mp4")


@pytest.mark.asyncio
async def test_secure_permissions_disabled(tmp_path: Path) -> None:
    """Repository with secure_permissions=False should not set restrictive permissions."""

    # Create repository with secure permissions disabled
    repo = VaultRepository(tmp_path, secure_permissions=False)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    # Create a note
    await repo.append_entry(note_dt, "Test entry")


@pytest.mark.asyncio
async def test_update_marked_entry_replaces_existing_payload(tmp_path: Path) -> None:
    """Marker-based updates should replace only the marker-delimited payload."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    marker = "1:10"

    await repo.append_entry(
        note_dt,
        "\n".join(
            [
                "%% 18:34:42 %%",
                marker_start_comment(marker),
                "old text",
                marker_end_comment(marker),
            ]
        ),
    )

    updated = await repo.update_marked_entry(
        note_dt,
        marker,
        "new text",
        frontmatter_updates={"mood": 4},
    )
    assert updated

    content = (await repo.get_note_content(note_dt)) or ""
    assert "new text" in content
    assert "old text" not in content
    assert "%% 18:34:42 %%" in content
    assert "mood: 4" in content


@pytest.mark.asyncio
async def test_update_marked_entry_returns_false_when_missing(tmp_path: Path) -> None:
    """Marker updates should fail safely when the marker block is absent."""
    repo = VaultRepository(tmp_path)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    await repo.append_entry(note_dt, "%% 18:34:42 %%\nplain entry")

    updated = await repo.update_marked_entry(note_dt, "1:999", "replacement")
    assert not updated

    # Year directory should exist but not have restrictive permissions set by us
    year_dir = tmp_path / "2026"
    assert year_dir.exists()
    # Note: We can't reliably test the exact permissions since umask affects them,
    # but we can verify the repository doesn't error out

    # Create a photo attachment
    async def mock_download(path: Path) -> None:
        path.write_text("photo_data", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock(side_effect=mock_download))
    photo = SimpleNamespace(get_file=AsyncMock(return_value=downloader))

    rel_path = await repo.save_photo(photo, note_dt, "20260307_183442")  # type: ignore[arg-type]
    assert rel_path.endswith("20260307_183442.jpg")

    # Verify file exists (permissions will be umask-dependent)
    photo_path = tmp_path / "2026" / "attachments" / "20260307_183442.jpg"
    assert photo_path.exists()


@pytest.mark.asyncio
async def test_secure_permissions_enabled(tmp_path: Path) -> None:
    """Repository with secure_permissions=True should set restrictive permissions."""

    # Create repository with secure permissions enabled (default)
    repo = VaultRepository(tmp_path, secure_permissions=True)
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    # Verify vault root has restrictive permissions
    vault_perms = tmp_path.stat().st_mode & 0o777
    assert vault_perms == 0o700, f"Expected 0o700, got {oct(vault_perms)}"

    # Create a note
    await repo.append_entry(note_dt, "Test entry")

    # Year directory should have restrictive permissions
    year_dir = tmp_path / "2026"
    year_perms = year_dir.stat().st_mode & 0o777
    assert year_perms == 0o700, f"Expected 0o700, got {oct(year_perms)}"

    # Note file should have restrictive permissions
    note_path = repo.get_note_path(note_dt)
    note_perms = note_path.stat().st_mode & 0o777
    assert note_perms == 0o600, f"Expected 0o600, got {oct(note_perms)}"

    # Create a photo attachment
    async def mock_download(path: Path) -> None:
        path.write_text("photo_data", encoding="utf-8")

    downloader = SimpleNamespace(download_to_drive=AsyncMock(side_effect=mock_download))
    photo = SimpleNamespace(get_file=AsyncMock(return_value=downloader))

    await repo.save_photo(photo, note_dt, "20260307_183442")  # type: ignore[arg-type]

    # Attachments directory should have restrictive permissions
    attachments_dir = tmp_path / "2026" / "attachments"
    attachments_perms = attachments_dir.stat().st_mode & 0o777
    assert attachments_perms == 0o700, f"Expected 0o700, got {oct(attachments_perms)}"

    # Photo file should have restrictive permissions
    photo_path = attachments_dir / "20260307_183442.jpg"
    photo_perms = photo_path.stat().st_mode & 0o777
    assert photo_perms == 0o600, f"Expected 0o600, got {oct(photo_perms)}"


@pytest.mark.asyncio
async def test_get_same_day_previous_year_notes(tmp_path: Path) -> None:
    """Historical lookup should return all prior-year same-day note contents."""
    repo = VaultRepository(tmp_path)
    reference_dt = datetime(2026, 3, 16, 9, 0, 0, tzinfo=UTC)

    year_2024 = tmp_path / "2024"
    year_2025 = tmp_path / "2025"
    year_2026 = tmp_path / "2026"
    for year_dir in (year_2024, year_2025, year_2026):
        year_dir.mkdir(parents=True, exist_ok=True)

    (year_2024 / "2024-03-16.md").write_text("from 2024", encoding="utf-8")
    (year_2025 / "2025-03-16.md").write_text("from 2025", encoding="utf-8")
    (year_2025 / "2025-03-15.md").write_text("wrong date", encoding="utf-8")
    (year_2026 / "2026-03-16.md").write_text("current year", encoding="utf-8")

    notes = await repo.get_same_day_previous_year_notes(reference_dt)

    assert [note_dt.year for note_dt, _ in notes] == [2024, 2025]
    assert [content for _, content in notes] == ["from 2024", "from 2025"]


@pytest.mark.asyncio
async def test_get_same_day_previous_year_notes_skips_non_file_paths(
    tmp_path: Path,
) -> None:
    """Historical lookup should ignore matching paths that are directories."""
    repo = VaultRepository(tmp_path)
    reference_dt = datetime(2026, 3, 16, 9, 0, 0, tzinfo=UTC)

    year_2024 = tmp_path / "2024"
    year_2024.mkdir(parents=True, exist_ok=True)
    (year_2024 / "2024-03-16.md").mkdir(parents=True, exist_ok=True)

    notes = await repo.get_same_day_previous_year_notes(reference_dt)
    assert notes == []


@pytest.mark.asyncio
async def test_get_same_day_previous_year_notes_skips_non_year_entries(
    tmp_path: Path,
) -> None:
    """Historical lookup should ignore files and non-numeric directories."""
    repo = VaultRepository(tmp_path)
    reference_dt = datetime(2026, 3, 16, 9, 0, 0, tzinfo=UTC)

    (tmp_path / "README.md").write_text("ignore me", encoding="utf-8")
    (tmp_path / "notes").mkdir(parents=True, exist_ok=True)

    notes = await repo.get_same_day_previous_year_notes(reference_dt)
    assert notes == []
