"""Unit tests for vault storage behavior."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib import error as urllib_error

import pytest
import yaml

from telejournal.formatting import marker_end_comment, marker_start_comment
from telejournal.config import Settings
from telejournal.storage import (
    GitHubRepository,
    NoteData,
    VaultRepository,
    build_repository,
)


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


def test_build_repository_obsidian_provider(tmp_path: Path) -> None:
    """Factory should build local vault repository for obsidian provider."""
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        storage_provider="obsidian_vault",
    )

    repo = build_repository(settings)
    assert isinstance(repo, VaultRepository)


def test_build_repository_github_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory should build GitHub repository for github provider settings."""
    monkeypatch.setattr(
        GitHubRepository, "_warn_if_repository_is_public", lambda _s: None
    )
    settings = Settings(
        telegram_token="token",
        vault_root=Path("."),
        allowed_user_ids={1},
        storage_provider="github_repo",
        github_owner="acme",
        github_repo="journal",
        github_token="token",
    )

    repo = build_repository(settings)
    assert isinstance(repo, GitHubRepository)


def test_build_repository_rejects_incomplete_github_settings() -> None:
    """Factory should reject incomplete github provider configuration."""
    settings = Settings(
        telegram_token="token",
        vault_root=Path("."),
        allowed_user_ids={1},
        storage_provider="github_repo",
    )
    with pytest.raises(ValueError, match="incomplete"):
        build_repository(settings)


def test_build_repository_rejects_unknown_provider(tmp_path: Path) -> None:
    """Factory should reject unsupported providers."""
    settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        storage_provider="other",
    )
    with pytest.raises(ValueError, match="Unsupported storage provider"):
        build_repository(settings)


def test_github_repository_warns_on_public_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public repositories should trigger an explicit warning message."""
    warnings: list[str] = []

    def _fake_request(
        self: GitHubRepository,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        del self, method, endpoint, payload, allow_not_found
        return {"private": False}

    monkeypatch.setattr(GitHubRepository, "_request_json", _fake_request)
    monkeypatch.setattr(
        "telejournal.storage.LOGGER.warning",
        lambda message, *args: warnings.append(message % args),
    )
    GitHubRepository("acme", "journal", "token")
    assert any("is public" in item for item in warnings)


def test_github_repository_warns_when_visibility_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visibility lookup failures should be logged as warnings."""
    warnings: list[str] = []

    def _raise_request(
        self: GitHubRepository,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        del self, method, endpoint, payload, allow_not_found
        raise RuntimeError("boom")

    monkeypatch.setattr(GitHubRepository, "_request_json", _raise_request)
    monkeypatch.setattr(
        "telejournal.storage.LOGGER.warning",
        lambda message, *args: warnings.append(message % args),
    )
    GitHubRepository("acme", "journal", "token")
    assert any("Could not verify" in item for item in warnings)


def test_github_request_json_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request helper should parse JSON, allow 404, and raise on other errors."""
    monkeypatch.setattr(
        GitHubRepository, "_warn_if_repository_is_public", lambda _s: None
    )
    repo = GitHubRepository("acme", "journal", "token")

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    monkeypatch.setattr(
        "telejournal.storage.urllib_request.urlopen", lambda *_a, **_k: _Resp()
    )
    payload = repo._request_json("GET", "/x")
    assert payload == {"ok": True}

    http_404 = urllib_error.HTTPError(
        url="http://x",
        code=404,
        msg="not found",
        hdrs=None,
        fp=None,
    )
    monkeypatch.setattr(
        "telejournal.storage.urllib_request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(http_404),
    )
    assert repo._request_json("GET", "/x", allow_not_found=True) is None

    http_500 = urllib_error.HTTPError(
        url="http://x",
        code=500,
        msg="error",
        hdrs=None,
        fp=None,
    )
    monkeypatch.setattr(
        "telejournal.storage.urllib_request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(http_500),
    )
    with pytest.raises(RuntimeError, match="GitHub API request failed"):
        repo._request_json("GET", "/x")

    monkeypatch.setattr(
        "telejournal.storage.urllib_request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")),
    )
    with pytest.raises(RuntimeError, match="GitHub API request failed"):
        repo._request_json("GET", "/x")


@pytest.mark.asyncio
async def test_github_repository_note_and_media_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub repository provider should support note CRUD and media flows."""
    monkeypatch.setattr(
        GitHubRepository, "_warn_if_repository_is_public", lambda _s: None
    )
    repo = GitHubRepository("acme", "journal", "token", path_prefix="notes")
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    store: dict[str, tuple[str, str]] = {}
    seq = {"sha": 0}

    def _sha() -> str:
        seq["sha"] += 1
        return f"sha-{seq['sha']}"

    def _get_content(repo_path: str) -> dict[str, Any] | None:
        item = store.get(repo_path)
        if item is None:
            return None
        content, sha = item
        return {
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
        }

    def _put_content(
        repo_path: str,
        payload_bytes: bytes,
        message: str,
        sha: str | None,
    ) -> None:
        del message, sha
        store[repo_path] = (payload_bytes.decode("utf-8"), _sha())

    def _delete_content(repo_path: str, sha: str) -> bool:
        del sha
        if repo_path not in store:
            return False
        del store[repo_path]
        return True

    def _request_json(
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        del method, payload, allow_not_found
        if endpoint.startswith("/repos/acme/journal/contents/notes?"):
            return [
                {"type": "dir", "name": "2025"},
                {"type": "dir", "name": "2026"},
                {"type": "file", "name": "README.md"},
            ]
        return None

    monkeypatch.setattr(repo, "_get_content", _get_content)
    monkeypatch.setattr(repo, "_put_content", _put_content)
    monkeypatch.setattr(repo, "_delete_content", _delete_content)
    monkeypatch.setattr(repo, "_request_json", _request_json)

    note_path = repo._repo_path(repo._note_relpath(note_dt))
    await repo.append_entry(note_dt, "%% 18:34:42 %%\nhello")
    # Writes are queued and flushed in a background batch.
    assert note_path not in store

    fm = await repo.get_note_frontmatter(note_dt)
    assert fm["tags"] == ["journal"]

    content = await repo.get_note_content(note_dt)
    assert content is not None
    assert "hello" in content

    await repo.flush_pending(reason="test")
    assert note_path in store

    await repo.update_frontmatter(note_dt, {"mood": 5})
    assert await repo.note_has_mood(note_dt)
    assert await repo.note_has_entry(note_dt)

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
    updated = await repo.update_marked_entry(note_dt, marker, "new text")
    assert updated
    assert not await repo.update_marked_entry(note_dt, "missing", "new")

    await repo.append_entry(note_dt, "%% 18:35:00 %%\nsecond")
    assert await repo.peek_last_entry(note_dt) == "%% 18:35:00 %%\nsecond"
    removed = await repo.delete_last_entry(note_dt)
    assert removed == "%% 18:35:00 %%\nsecond"

    last_time = await repo.get_last_entry_time(note_dt)
    assert last_time == datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    previous_dt = datetime(2025, 3, 7, 1, 0, 0, tzinfo=UTC)
    previous_path = repo._repo_path(repo._note_relpath(previous_dt))
    store[previous_path] = (
        "---\nmood: null\n---\n\nfrom history\n",
        _sha(),
    )
    historical = await repo.get_same_day_previous_year_notes(note_dt)
    assert [item[0].year for item in historical] == [2025]

    assert await repo.delete_day(note_dt)
    assert not await repo.delete_day(note_dt)

    # attachment bytes retrieval
    attachment_key = repo._repo_path("2026/attachments/a.jpg")
    store[attachment_key] = ("binary-data", _sha())
    attachment = await repo.get_attachment_bytes("2026/attachments/a.jpg")
    assert attachment == b"binary-data"


@pytest.mark.asyncio
async def test_github_repository_media_paths_and_download_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Media save methods should use bytearray path and drive-download fallback."""
    monkeypatch.setattr(
        GitHubRepository, "_warn_if_repository_is_public", lambda _s: None
    )
    repo = GitHubRepository("acme", "journal", "token")
    note_dt = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)

    existing: set[str] = {repo._repo_path("2026/attachments/20260307_183442.jpg")}
    uploaded: list[str] = []

    def _get_content(repo_path: str) -> dict[str, Any] | None:
        if repo_path in existing:
            return {"content": "", "sha": "x"}
        return None

    def _put_content(
        repo_path: str,
        payload_bytes: bytes,
        message: str,
        sha: str | None,
    ) -> None:
        del payload_bytes, message, sha
        uploaded.append(repo_path)
        existing.add(repo_path)

    monkeypatch.setattr(repo, "_get_content", _get_content)
    monkeypatch.setattr(repo, "_put_content", _put_content)

    photo_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"p"))
    photo = SimpleNamespace(get_file=AsyncMock(return_value=photo_file))
    voice_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"v"))
    voice = SimpleNamespace(get_file=AsyncMock(return_value=voice_file))
    video_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"m"))
    video = SimpleNamespace(get_file=AsyncMock(return_value=video_file))
    video_note_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=b"n")
    )
    video_note = SimpleNamespace(get_file=AsyncMock(return_value=video_note_file))

    photo_rel = await repo.save_photo(photo, note_dt, "20260307_183442")  # type: ignore[arg-type]
    voice_rel = await repo.save_voice(voice, note_dt, "20260307_183442")  # type: ignore[arg-type]
    video_rel = await repo.save_video(video, note_dt, "20260307_183442")  # type: ignore[arg-type]
    note_rel = await repo.save_video_note(video_note, note_dt, "20260307_183442")  # type: ignore[arg-type]

    assert photo_rel.endswith("_1.jpg")
    assert voice_rel.endswith(".ogg")
    assert video_rel.endswith(".mp4")
    assert note_rel.endswith("_note.mp4")
    assert not uploaded

    await repo.flush_pending(reason="test")
    assert uploaded

    # Cover download_to_drive fallback path
    async def _download_to_drive(path: Path) -> None:
        path.write_bytes(b"fallback")

    fallback_file = SimpleNamespace(download_to_drive=_download_to_drive)
    fallback_media = SimpleNamespace(get_file=AsyncMock(return_value=fallback_file))
    payload = await repo._download_media_bytes(fallback_media)
    assert payload == b"fallback"


@pytest.mark.asyncio
async def test_github_repository_remaining_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise remaining GitHub repository branches for full coverage."""
    monkeypatch.setattr(
        GitHubRepository,
        "_warn_if_repository_is_public",
        lambda _s: None,
    )
    repo = GitHubRepository("acme", "journal", "token")

    assert repo.vault_root == Path("/")

    class _EmptyResp:
        def __enter__(self) -> "_EmptyResp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b""

    monkeypatch.setattr(
        "telejournal.storage.urllib_request.urlopen",
        lambda *_a, **_k: _EmptyResp(),
    )
    assert repo._request_json("PUT", "/x", payload={"a": 1}) is None

    with patch("telejournal.storage.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 7, 12, 0, 0, tzinfo=UTC)
        mock_dt.combine = datetime.combine
        mock_dt.min.time = datetime.min.time
        mock_dt.UTC = UTC
        defaults = repo._default_frontmatter(datetime(2026, 3, 7, 1, 0, tzinfo=UTC))
        assert defaults["created"] == "2026-03-07T12:00:00Z"

    assert repo._split_frontmatter("plain body") == ({}, "plain body")
    assert repo._split_frontmatter("---\na: 1\n") == ({}, "---\na: 1\n")
    assert repo._split_frontmatter("---\n- a\n---\n\nbody") == ({}, "body")
    assert repo.get_note_path(datetime(2026, 3, 7, tzinfo=UTC)) == Path(
        "2026/2026-03-07.md"
    )
    assert repo._decode_content({}) == b""

    monkeypatch.setattr(repo, "_request_json", lambda *_a, **_k: [])
    with pytest.raises(RuntimeError, match="Unexpected GitHub contents payload"):
        repo._get_content("x")

    monkeypatch.setattr(repo, "_request_json", lambda *_a, **_k: None)
    assert repo._get_content("x") is None
    monkeypatch.setattr(repo, "_request_json", lambda *_a, **_k: {"ok": True})
    assert repo._get_content("x") == {"ok": True}

    captured_payloads: list[dict[str, Any]] = []

    def _capture_request(
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        del endpoint, allow_not_found
        if payload is not None:
            captured_payloads.append(payload)
        if method == "DELETE":
            return None
        return {"ok": True}

    monkeypatch.setattr(repo, "_request_json", _capture_request)
    repo._put_content("x.md", b"data", "msg", "sha-1")
    assert captured_payloads[-1]["sha"] == "sha-1"
    assert repo._delete_content("x.md", "sha-1") is False

    written: dict[str, Any] = {}

    async def _fake_read_note(_note_path: str) -> tuple[NoteData, str | None]:
        return NoteData(frontmatter={}, body="line1"), "sha"

    async def _fake_write_note(
        _note_path: str,
        frontmatter: dict[str, Any],
        body: str,
        _sha: str | None,
    ) -> None:
        written["frontmatter"] = frontmatter
        written["body"] = body

    monkeypatch.setattr(repo, "_read_note", _fake_read_note)
    monkeypatch.setattr(repo, "_write_note", _fake_write_note)
    await repo.append_entry(
        datetime(2026, 3, 7, tzinfo=UTC),
        "line2",
        {"mood": 2},
        as_continuation=True,
    )
    assert written["frontmatter"]["mood"] == 2
    assert written["body"] == "line1\nline2"

    marker = "1:1"

    async def _read_marked(_note_path: str) -> tuple[NoteData, str | None]:
        body = "\n".join(
            [marker_start_comment(marker), "old", marker_end_comment(marker)]
        )
        return NoteData(frontmatter={}, body=body), "sha"

    monkeypatch.setattr(repo, "_read_note", _read_marked)
    await repo.update_marked_entry(
        datetime(2026, 3, 7, tzinfo=UTC),
        marker,
        "new",
        {"mood": 5},
    )
    assert written["frontmatter"]["mood"] == 5

    monkeypatch.setattr(
        repo,
        "_read_note",
        AsyncMock(return_value=(NoteData(frontmatter={}, body=""), None)),
    )
    assert await repo.get_note_content(datetime(2026, 3, 7, tzinfo=UTC)) is None

    def _history_request(
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        del method, endpoint, payload, allow_not_found
        return [
            "bad",
            {"type": "file"},
            {"type": "dir", "name": "x"},
            {"type": "dir", "name": "2025"},
        ]

    monkeypatch.setattr(repo, "_request_json", _history_request)
    monkeypatch.setattr(
        repo,
        "_read_note",
        AsyncMock(return_value=(NoteData(frontmatter={}, body=""), None)),
    )
    assert (
        await repo.get_same_day_previous_year_notes(datetime(2026, 3, 7, tzinfo=UTC))
        == []
    )

    monkeypatch.setattr(repo, "_request_json", lambda *_a, **_k: None)
    assert (
        await repo.get_same_day_previous_year_notes(datetime(2026, 3, 7, tzinfo=UTC))
        == []
    )
    monkeypatch.setattr(
        repo,
        "_read_note",
        AsyncMock(return_value=(NoteData(frontmatter={}, body=""), "sha")),
    )
    assert await repo.delete_last_entry(datetime(2026, 3, 7, tzinfo=UTC)) is None
    assert await repo.peek_last_entry(datetime(2026, 3, 7, tzinfo=UTC)) is None

    monkeypatch.setattr(repo, "_get_content", lambda _p: None)
    assert not await repo.delete_day(datetime(2026, 3, 7, tzinfo=UTC))

    monkeypatch.setattr(
        repo,
        "get_note_frontmatter",
        AsyncMock(return_value={"mood": [{"value": "x"}]}),
    )
    assert not await repo.note_has_mood(datetime(2026, 3, 7, tzinfo=UTC))
    monkeypatch.setattr(
        repo,
        "get_note_frontmatter",
        AsyncMock(return_value={"mood": [{"value": 7}]}),
    )
    assert await repo.note_has_mood(datetime(2026, 3, 7, tzinfo=UTC))
    monkeypatch.setattr(
        repo,
        "get_note_frontmatter",
        AsyncMock(return_value={"mood": {"value": 3}}),
    )
    assert await repo.note_has_mood(datetime(2026, 3, 7, tzinfo=UTC))
    monkeypatch.setattr(
        repo,
        "get_note_frontmatter",
        AsyncMock(return_value={"mood": "yes"}),
    )
    assert await repo.note_has_mood(datetime(2026, 3, 7, tzinfo=UTC))

    monkeypatch.setattr(
        repo,
        "_read_note",
        AsyncMock(return_value=(NoteData(frontmatter={}, body=""), None)),
    )
    assert await repo.get_last_entry_time(datetime(2026, 3, 7, tzinfo=UTC)) is None

    monkeypatch.setattr(repo, "_get_content", lambda _p: None)
    assert await repo.get_attachment_bytes("2026/attachments/x") is None


@pytest.mark.asyncio
async def test_github_repository_batch_retry_and_delete_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed flush operations should be re-queued and retried on next flush."""
    monkeypatch.setattr(
        GitHubRepository,
        "_warn_if_repository_is_public",
        lambda _s: None,
    )
    repo = GitHubRepository("acme", "journal", "token")
    monkeypatch.setattr(repo, "_ensure_flush_task", lambda: None)

    puts: list[str] = []
    deletes: list[str] = []
    state = {"fail_once": True}

    monkeypatch.setattr(repo, "_get_content", lambda _p: {"sha": "sha-1"})

    def _put_content(
        repo_path: str,
        payload_bytes: bytes,
        message: str,
        sha: str | None,
    ) -> None:
        del payload_bytes, message, sha
        if state["fail_once"]:
            state["fail_once"] = False
            raise RuntimeError("transient")
        puts.append(repo_path)

    def _delete_content(repo_path: str, sha: str) -> bool:
        del sha
        deletes.append(repo_path)
        return True

    monkeypatch.setattr(repo, "_put_content", _put_content)
    monkeypatch.setattr(repo, "_delete_content", _delete_content)

    await repo._queue_put_content("2026/2026-03-07.md", b"hello", "update")
    await repo._queue_delete_content("2026/2026-03-06.md")

    await repo.flush_pending(reason="test")
    assert deletes == ["2026/2026-03-06.md"]
    assert puts == []

    await repo.flush_pending(reason="retry")
    assert puts == ["2026/2026-03-07.md"]


@pytest.mark.asyncio
async def test_github_repository_flush_loop_starts_once_and_handles_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flush worker should start once, flush periodically, and handle cancel."""
    monkeypatch.setattr(
        GitHubRepository,
        "_warn_if_repository_is_public",
        lambda _s: None,
    )
    repo = GitHubRepository("acme", "journal", "token", batch_window_seconds=1)

    started: list[asyncio.Task[None]] = []

    def _create_task(coro: Any, name: str | None = None) -> asyncio.Task[None]:
        coro.close()
        del coro

        class _DummyTask:
            def done(self) -> bool:
                return False

        task = _DummyTask()
        started.append(task)  # type: ignore[arg-type]
        return task  # type: ignore[return-value]

    monkeypatch.setattr("telejournal.storage.asyncio.create_task", _create_task)
    repo._ensure_flush_task()
    repo._ensure_flush_task()
    assert len(started) == 1

    calls: list[str] = []

    async def _flush_pending(reason: str = "manual") -> None:
        calls.append(reason)
        raise asyncio.CancelledError()

    async def _sleep(_seconds: int) -> None:
        return None

    monkeypatch.setattr(repo, "flush_pending", _flush_pending)
    monkeypatch.setattr("telejournal.storage.asyncio.sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await repo._flush_loop()
    assert calls == ["timer"]


@pytest.mark.asyncio
async def test_github_repository_queue_edge_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover queue edge branches for pending reads, deletes, and media checks."""
    monkeypatch.setattr(
        GitHubRepository,
        "_warn_if_repository_is_public",
        lambda _s: None,
    )
    repo = GitHubRepository("acme", "journal", "token")
    monkeypatch.setattr(repo, "_ensure_flush_task", lambda: None)

    # Empty queue flush should take the fast-return debug path.
    await repo.flush_pending(reason="empty")

    # Cover _flush_delete_content paths: no remote content and missing sha.
    monkeypatch.setattr(repo, "_get_content", lambda _p: None)
    repo._flush_delete_content("x")
    monkeypatch.setattr(repo, "_get_content", lambda _p: {"sha": ""})
    with pytest.raises(RuntimeError, match="Could not resolve sha"):
        repo._flush_delete_content("x")

    # Cover _flush_loop generic exception branch, then cancel to stop loop.
    calls = {"count": 0}

    async def _flush_pending(reason: str = "manual") -> None:
        del reason
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError()

    async def _sleep(_seconds: int) -> None:
        return None

    monkeypatch.setattr(repo, "flush_pending", _flush_pending)
    monkeypatch.setattr("telejournal.storage.asyncio.sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        await repo._flush_loop()

    # _read_note pending-delete short circuit.
    await repo._queue_delete_content("2026/2026-03-07.md")
    note_data, sha = await repo._read_note("2026/2026-03-07.md")
    assert note_data.frontmatter == {}
    assert note_data.body == ""
    assert sha is None

    # delete_day path where remote exists and delete is queued.
    monkeypatch.setattr(repo, "_get_content", lambda _p: {"sha": "sha-1"})
    deleted = await repo.delete_day(datetime(2026, 3, 8, tzinfo=UTC))
    assert deleted
    assert "2026/2026-03-08.md" in repo._pending_deletes

    # _save_media branch for pending delete returns immediately.
    await repo._queue_delete_content("2026/attachments/20260307_183442.jpg")
    rel_deleted = await repo._save_media(
        datetime(2026, 3, 7, tzinfo=UTC),
        "20260307_183442",
        ".jpg",
    )
    assert rel_deleted == "2026/attachments/20260307_183442.jpg"

    # _save_media queued-collision branch increments suffix.
    await repo._queue_put_content(
        "2026/attachments/20260307_183443.jpg",
        b"queued",
        "msg",
    )
    monkeypatch.setattr(repo, "_get_content", lambda _p: None)
    rel_queued = await repo._save_media(
        datetime(2026, 3, 7, tzinfo=UTC),
        "20260307_183443",
        ".jpg",
    )
    assert rel_queued == "2026/attachments/20260307_183443_1.jpg"

    # get_attachment_bytes queued and pending-delete branches.
    await repo._queue_put_content("2026/attachments/q.jpg", b"raw", "msg")
    assert await repo.get_attachment_bytes("2026/attachments/q.jpg") == b"raw"
    await repo._queue_delete_content("2026/attachments/d.jpg")
    assert await repo.get_attachment_bytes("2026/attachments/d.jpg") is None


@pytest.mark.asyncio
async def test_github_repository_flush_requeues_delete_when_no_pending_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed delete flushes should re-queue delete paths for next cycle."""
    monkeypatch.setattr(
        GitHubRepository,
        "_warn_if_repository_is_public",
        lambda _s: None,
    )
    repo = GitHubRepository("acme", "journal", "token")
    monkeypatch.setattr(repo, "_ensure_flush_task", lambda: None)

    await repo._queue_delete_content("2026/2026-03-10.md")

    def _raise_delete(_path: str) -> None:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(repo, "_flush_delete_content", _raise_delete)
    await repo.flush_pending(reason="test")

    assert "2026/2026-03-10.md" in repo._pending_deletes
