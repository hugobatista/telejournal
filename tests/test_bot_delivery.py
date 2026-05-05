"""Tests for note delivery attachment sending paths."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telejournal.bot_delivery import NoteDeliveryService


@pytest.mark.asyncio
async def test_send_attachment_uses_remote_attachment_bytes() -> None:
    """Delivery should send in-memory attachment bytes for remote providers."""
    repo = SimpleNamespace(
        vault_root=Path("/"),
        get_attachment_bytes=AsyncMock(return_value=b"img"),
    )
    tracked: list[int] = []
    service = NoteDeliveryService(
        repository_provider=lambda: repo,
        track_reply_source_message=lambda chat_id, _sent, _dt: tracked.append(chat_id),
        no_memories_message="none",
        logger=logging.getLogger("test"),
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        send_video=AsyncMock(),
        send_voice=AsyncMock(),
        send_document=AsyncMock(),
        send_message=AsyncMock(),
    )

    await service.send_attachment(
        7,
        bot,
        "2026/attachments/image.jpg",
        source_note_dt=datetime(2026, 3, 7, tzinfo=UTC),
    )

    assert bot.send_photo.await_count == 1
    assert tracked == [7]


@pytest.mark.asyncio
async def test_send_attachment_remote_missing_and_send_failure() -> None:
    """Delivery should report not-found and send failures for remote files."""
    repo = SimpleNamespace(
        vault_root=Path("/"),
        get_attachment_bytes=AsyncMock(side_effect=[None, b"doc"]),
    )
    service = NoteDeliveryService(
        repository_provider=lambda: repo,
        track_reply_source_message=lambda *_args: None,
        no_memories_message="none",
        logger=logging.getLogger("test"),
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_video=AsyncMock(),
        send_voice=AsyncMock(),
        send_document=AsyncMock(side_effect=OSError("send failed")),
        send_message=AsyncMock(),
    )

    await service.send_attachment(1, bot, "2026/attachments/missing.bin")
    await service.send_attachment(1, bot, "2026/attachments/file.bin")

    messages = [call.args[1] for call in bot.send_message.await_args_list]
    assert any("Attachment not found" in msg for msg in messages)
    assert any("Failed to send attachment" in msg for msg in messages)


@pytest.mark.asyncio
async def test_send_attachment_uses_note_relative_path_for_local_vault() -> None:
    """Local vault delivery should resolve paths relative to the note directory."""
    vault_root = Path("/tmp/telejournal-test-vault")
    note_dt = datetime(2026, 5, 5, tzinfo=UTC)
    attachment_path = vault_root / "2026" / "resources" / "20260505_161408.jpg"
    attachment_path.parent.mkdir(parents=True, exist_ok=True)
    attachment_path.write_bytes(b"img")

    repo = SimpleNamespace(
        vault_root=vault_root,
        get_note_path=lambda _dt: vault_root / "2026" / "2026-05-05.md",
    )
    service = NoteDeliveryService(
        repository_provider=lambda: repo,
        track_reply_source_message=lambda *_args: None,
        no_memories_message="none",
        logger=logging.getLogger("test"),
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        send_video=AsyncMock(),
        send_voice=AsyncMock(),
        send_document=AsyncMock(),
        send_message=AsyncMock(),
    )

    await service.send_attachment(
        7,
        bot,
        "resources/20260505_161408.jpg",
        source_note_dt=note_dt,
    )

    assert bot.send_photo.await_count == 1
    assert bot.send_message.await_count == 0
