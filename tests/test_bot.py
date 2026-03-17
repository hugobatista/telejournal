"""Behavioral tests for JournalBot handlers and callbacks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatType

from telejournal.bot import (
    ACTIVE_CHATS_KEY,
    ALBUMS_KEY,
    DAILY_BRIEF_JOB_NAME,
    DELETE_CALLBACK_PREFIX,
    HISTORY_CALLBACK_PREFIX,
    LAST_PROMPT_AT_KEY,
    LAST_WINDOW_AT_KEY,
    MAX_TRACKED_REPLY_SOURCES,
    MOOD_CALLBACK_PREFIX,
    NO_MEMORIES_MESSAGE,
    STARTUP_JOB_NAME,
    STARTUP_MESSAGE,
    TAG_CALLBACK_PREFIX,
    JournalBot,
    _mood_keyboard,
    _tags_keyboard,
    _chunk_text,
    _truncate_message,
)
from telejournal.config import Settings
from telejournal.formatting import extract_mood_value


class _FakeJobQueue:
    def __init__(self) -> None:
        self.once_jobs: dict[str, dict[str, Any]] = {}
        self.repeat: dict[str, Any] = {}
        self.daily: dict[str, Any] = {}

    def get_jobs_by_name(self, name: str) -> list[object]:
        if name in self.once_jobs:
            return [object()]
        return []

    def run_once(
        self,
        callback: object,
        *,
        when: int,
        data: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        job_name = name or f"job-{len(self.once_jobs)}"
        self.once_jobs[job_name] = {
            "callback": callback,
            "when": when,
            "data": data,
            "name": job_name,
        }

    def run_repeating(self, callback: object, *, interval: int, first: int) -> None:
        self.repeat = {"callback": callback, "interval": interval, "first": first}

    def run_daily(self, callback: object, *, time: object, name: str) -> None:
        self.daily[name] = {"callback": callback, "time": time, "name": name}


@pytest.fixture
def journal_bot(tmp_path: Path) -> JournalBot:
    """Create bot with fake repository methods for handler testing."""
    bot = JournalBot(Settings("token", tmp_path, {1}))
    bot._repository = SimpleNamespace(  # type: ignore[assignment]
        vault_root=tmp_path,
        append_entry=AsyncMock(),
        delete_last_entry=AsyncMock(return_value="%% 18:34:42 %%\nhello"),
        delete_day=AsyncMock(return_value=True),
        peek_last_entry=AsyncMock(return_value="%% 18:34:42 %%\nhello"),
        get_note_content=AsyncMock(
            return_value="---\nmood: 3\n---\n\n%% 18:00:00 %%\nhi\n"
        ),
        save_photo=AsyncMock(return_value="2026/attachments/ts.jpg"),
        save_voice=AsyncMock(return_value="2026/attachments/voice.ogg"),
        save_video=AsyncMock(return_value="2026/attachments/video.mp4"),
        save_video_note=AsyncMock(return_value="2026/attachments/video_note.mp4"),
        get_note_frontmatter=AsyncMock(
            return_value={"tags": ["journal", "work"], "mood": None}
        ),
        update_frontmatter=AsyncMock(),
        note_has_entry=AsyncMock(return_value=True),
        note_has_mood=AsyncMock(return_value=False),
        get_last_entry_time=AsyncMock(
            return_value=datetime.now(UTC) - timedelta(hours=6)
        ),
        get_same_day_previous_year_notes=AsyncMock(return_value=[]),
    )
    return bot


def _context(
    *,
    args: list[str] | None = None,
    with_job_queue: bool = True,
) -> SimpleNamespace:
    """Build a minimal PTB-like context object."""
    job_queue = _FakeJobQueue() if with_job_queue else None
    return SimpleNamespace(
        args=args,
        chat_data={},
        application=SimpleNamespace(bot_data={}, chat_data={}),
        job_queue=job_queue,
        bot=SimpleNamespace(
            send_message=AsyncMock(),
            send_photo=AsyncMock(),
            send_video=AsyncMock(),
            send_voice=AsyncMock(),
            send_document=AsyncMock(),
        ),
        job=None,
        error=RuntimeError("boom"),
    )


def _private_update(
    *,
    user_id: int = 1,
    text: str | None = "hi",
    caption: str | None = None,
    photo: list[object] | None = None,
    voice: object | None = None,
    video: object | None = None,
    video_note: object | None = None,
    location: object | None = None,
    media_group_id: str | None = None,
    callback_data: str | None = None,
    message_id: int = 1,
    reply_to_message: object | None = None,
) -> SimpleNamespace:
    """Build a minimal private Update-like object."""
    message = SimpleNamespace(
        text=text,
        caption=caption,
        text_markdown_urled=text,
        caption_markdown_urled=caption,
        photo=photo,
        voice=voice,
        video=video,
        video_note=video_note,
        location=location,
        media_group_id=media_group_id,
        message_id=message_id,
        reply_text=AsyncMock(),
        reply_to_message=reply_to_message,
        from_user=SimpleNamespace(id=user_id),
    )
    callback_query = None
    if callback_data is not None:
        callback_query = SimpleNamespace(
            data=callback_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )

    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=1, type=ChatType.PRIVATE),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        callback_query=callback_query,
    )


@pytest.mark.asyncio
async def test_help_mood_setdate_resetdate_commands(journal_bot: JournalBot) -> None:
    """Core command handlers should return user-facing responses."""
    update = _private_update()
    context = _context(args=["2026-03-07"])

    await journal_bot.help_command(update, context)  # type: ignore
    await journal_bot.mood_command(update, context)  # type: ignore
    await journal_bot.setdate_command(update, context)  # type: ignore
    await journal_bot.resetdate_command(update, context)  # type: ignore
    await journal_bot.delete_command(update, context)  # type: ignore
    await journal_bot.show_command(update, context)  # type: ignore
    await journal_bot.todayinhistory_command(update, context)  # type: ignore

    assert update.effective_message.reply_text.await_count == 6


@pytest.mark.asyncio
async def test_setdate_invalid_input_replies_error(journal_bot: JournalBot) -> None:
    """Bad /setdate args should surface usage message."""
    update = _private_update()
    context = _context(args=["bad"])

    await journal_bot.setdate_command(update, context)

    assert "Use: /setdate" in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_tags_command_uses_frontmatter(journal_bot: JournalBot) -> None:
    """Tags command should show current tags and inline controls."""
    update = _private_update()
    context = _context()

    await journal_bot.tags_command(update, context)

    first_arg = update.effective_message.reply_text.await_args.args[0]
    assert "Current:" in first_arg


@pytest.mark.asyncio
async def test_tags_command_adds_custom_tags_from_args(journal_bot: JournalBot) -> None:
    """Tags command should accept free-form tags provided as args."""
    update = _private_update()
    context = _context(args=["kids", "work"])

    await journal_bot.tags_command(update, context)

    assert journal_bot._repository.update_frontmatter.await_count == 1  # type: ignore
    first_arg = update.effective_message.reply_text.await_args.args[0]
    assert "Updated:" in first_arg
    assert "kids" in first_arg


@pytest.mark.asyncio
async def test_handle_text_location_photo_and_album(journal_bot: JournalBot) -> None:
    """Message handler should route message types and schedule album flush."""
    context = _context()

    text_update = _private_update(text="hello")
    await journal_bot.handle_journal_entry(text_update, context)

    loc = SimpleNamespace(latitude=38.7223, longitude=-9.1393)
    location_update = _private_update(text=None, location=loc)
    await journal_bot.handle_journal_entry(location_update, context)

    class _Photo:
        async def get_file(self) -> object:
            return SimpleNamespace(download_to_drive=AsyncMock())

    photo_update = _private_update(text=None, photo=[_Photo()])
    await journal_bot.handle_journal_entry(photo_update, context)

    voice_update = _private_update(
        text=None,
        voice=SimpleNamespace(get_file=AsyncMock()),
    )
    await journal_bot.handle_journal_entry(voice_update, context)

    video_update = _private_update(
        text=None,
        video=SimpleNamespace(get_file=AsyncMock()),
    )
    await journal_bot.handle_journal_entry(video_update, context)

    video_note_update = _private_update(
        text=None,
        video_note=SimpleNamespace(get_file=AsyncMock()),
    )
    await journal_bot.handle_journal_entry(video_note_update, context)

    album_update = _private_update(
        text=None,
        caption="album",
        photo=[_Photo()],
        media_group_id="group-1",
    )
    await journal_bot.handle_journal_entry(album_update, context)

    assert journal_bot._repository.append_entry.await_count >= 3  # type: ignore
    assert context.job_queue.once_jobs


@pytest.mark.asyncio
async def test_message_timestamp_window(journal_bot: JournalBot) -> None:
    """Messages in the same window should suppress repeated timestamp prefixes."""
    context = _context()

    first = _private_update(text="first")
    await journal_bot.handle_journal_entry(first, context)

    second = _private_update(text="second")
    await journal_bot.handle_journal_entry(second, context)

    entries = [
        call.args[1]
        for call in journal_bot._repository.append_entry.await_args_list[:2]  # type: ignore
    ]
    assert entries[0].startswith("%% ")
    assert "\nfirst" in entries[0]
    assert entries[1] == "second"


@pytest.mark.asyncio
async def test_message_timestamp_window_rollover(journal_bot: JournalBot) -> None:
    """Timestamp should reset when window threshold is exceeded."""
    context = _context()
    now = datetime.now(UTC)
    context.chat_data[LAST_WINDOW_AT_KEY] = now - timedelta(seconds=61)
    context.chat_data["last_window_note"] = now.strftime("%Y-%m-%d")

    update = _private_update(text="new window")
    await journal_bot.handle_journal_entry(update, context)

    entry = journal_bot._repository.append_entry.await_args.args[1]  # type: ignore
    assert entry.startswith("%% ")
    assert entry.endswith("\nnew window")


@pytest.mark.asyncio
async def test_entry_ack_and_initial_mood_prompt(journal_bot: JournalBot) -> None:
    """New journal entries should acknowledge write and ask for mood when missing."""
    context = _context()
    update = _private_update(text="hello")

    await journal_bot.handle_journal_entry(update, context)

    replies = [
        call.args[0] for call in update.effective_message.reply_text.await_args_list
    ]
    assert "✅ Added to journal." in replies
    assert "How are you feeling today?" in replies


@pytest.mark.asyncio
async def test_entry_ack_without_mood_prompt_when_mood_exists(
    journal_bot: JournalBot,
) -> None:
    """Entries should still acknowledge writes even when mood prompt is skipped."""
    journal_bot._repository.note_has_mood = AsyncMock(return_value=True)  # type: ignore
    context = _context()
    update = _private_update(text="hello")

    await journal_bot.handle_journal_entry(update, context)

    replies = [
        call.args[0] for call in update.effective_message.reply_text.await_args_list
    ]
    assert replies == ["✅ Added to journal."]


@pytest.mark.asyncio
async def test_setdate_uses_date_scoped_mood_prompt(journal_bot: JournalBot) -> None:
    """Mood prompt should still trigger when override date differs from last prompt date."""
    context = _context()
    context.chat_data[LAST_PROMPT_AT_KEY] = datetime.now(UTC)
    context.chat_data["last_prompt_note"] = "2026-03-07"
    context.chat_data["override_date"] = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)

    update = _private_update(text="hello")
    await journal_bot.handle_journal_entry(update, context)

    replies = [
        call.args[0] for call in update.effective_message.reply_text.await_args_list
    ]
    assert "How are you feeling today?" in replies


@pytest.mark.asyncio
async def test_delete_command_replies_with_deleted_entry(
    journal_bot: JournalBot,
) -> None:
    """Delete command without args should request confirmation first."""
    context = _context()
    update = _private_update()

    await journal_bot.delete_command(update, context)

    text = update.effective_message.reply_text.await_args.args[0]
    assert "Confirm deleting the last entry" in text
    assert "18:34:42" in text
    assert update.effective_message.reply_text.await_args.kwargs["reply_markup"]


@pytest.mark.asyncio
async def test_delete_command_day_argument(journal_bot: JournalBot) -> None:
    """Delete day command should request confirmation first."""
    context = _context(args=["day", "2026-03-06"])
    update = _private_update()

    await journal_bot.delete_command(update, context)

    assert journal_bot._repository.delete_day.await_count == 0  # type: ignore
    assert (
        "Confirm deleting day 2026-03-06"
        in update.effective_message.reply_text.await_args.args[0]
    )


@pytest.mark.asyncio
async def test_show_command_specific_day(journal_bot: JournalBot) -> None:
    """Show command should ask for output format with inline buttons."""
    context = _context(args=["2026-03-06"])
    update = _private_update()

    await journal_bot.show_command(update, context)

    assert journal_bot._repository.get_note_content.await_count == 1  # type: ignore
    text = update.effective_message.reply_text.await_args.args[0]
    assert "How do you want to view note" in text
    assert update.effective_message.reply_text.await_args.kwargs["reply_markup"]


@pytest.mark.asyncio
async def test_show_callback_sends_embedded_attachment(
    journal_bot: JournalBot,
    tmp_path: Path,
) -> None:
    """Show callback in rendered mode should send embedded media files."""
    attachment = tmp_path / "2026" / "attachments" / "pic.jpg"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_bytes(b"jpeg-bytes")

    journal_bot._repository.get_note_content = AsyncMock(  # type: ignore[attr-defined]
        return_value="Header\n![[2026/attachments/pic.jpg]]\nFooter"
    )
    context = _context()
    update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}show:rendered:2026-03-06"
    )

    await journal_bot.callback_router(update, context)

    assert update.callback_query.edit_message_text.await_count == 1
    assert context.bot.send_message.await_count == 2  # type: ignore[attr-defined]
    assert context.bot.send_photo.await_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_show_callback_sends_note_text_only(journal_bot: JournalBot) -> None:
    """Show callback in raw mode should send note text without rendering embeds."""
    journal_bot._repository.get_note_content = AsyncMock(  # type: ignore[attr-defined]
        return_value="A\n![[2026/attachments/pic.jpg]]\nB"
    )
    context = _context()
    update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}show:raw:2026-03-06"
    )

    await journal_bot.callback_router(update, context)

    payloads = [
        call.args[1] for call in context.bot.send_message.await_args_list  # type: ignore[attr-defined]
    ]
    assert any("![[2026/attachments/pic.jpg]]" in payload for payload in payloads)
    assert context.bot.send_photo.await_count == 0  # type: ignore[attr-defined]


def test_extract_mood_value_simple() -> None:
    """Mood extractor should handle int and invalid values."""
    assert extract_mood_value(None) is None
    assert extract_mood_value(3) == 3
    assert extract_mood_value(99) is None
    assert extract_mood_value({"value": 4}) is None
    assert extract_mood_value([{"value": 2}]) is None


def test_truncate_message_branch() -> None:
    """Long strings should be truncated and short strings should pass through."""
    assert _truncate_message("abc", max_len=10) == "abc"
    assert _truncate_message("x" * 20, max_len=10).endswith("...")


def test_chunk_text_branches() -> None:
    """Chunking helper should split lines and fallback to hard boundaries."""
    assert _chunk_text("abc", chunk_size=10) == ["abc"]

    with_newlines = "line1\nline2\nline3"
    chunks = _chunk_text(with_newlines, chunk_size=8)
    assert chunks == ["line1", "line2", "line3"]

    no_newline = "x" * 20
    chunks = _chunk_text(no_newline, chunk_size=7)
    assert chunks == ["xxxxxxx", "xxxxxxx", "xxxxxx"]

    leading_newline = "\n" + ("y" * 9)
    chunks = _chunk_text(leading_newline, chunk_size=5)
    assert chunks == ["\nyyyy", "yyyyy"]

    # Cover branch where rstrip() empties chunk and hard split is used.
    spaces_before_newline = "   \nabcdef"
    chunks = _chunk_text(spaces_before_newline, chunk_size=6)
    assert chunks == ["   \nab", "cdef"]


@pytest.mark.asyncio
async def test_show_command_error_paths(journal_bot: JournalBot) -> None:
    """Show command should validate args and handle missing note content."""
    update = _private_update()

    context_many = _context(args=["2026-01-01", "extra"])
    await journal_bot.show_command(update, context_many)
    assert "Use: /show" in update.effective_message.reply_text.await_args.args[0]

    context_bad = _context(args=["bad"])
    await journal_bot.show_command(update, context_bad)
    assert "Use: /show" in update.effective_message.reply_text.await_args.args[0]

    journal_bot._repository.get_note_content = AsyncMock(return_value=None)  # type: ignore
    context_missing = _context(args=["2026-03-06"])
    await journal_bot.show_command(update, context_missing)
    assert "No note found" in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_show_command_returns_when_message_missing(
    journal_bot: JournalBot,
) -> None:
    """Show command should no-op when effective_message is unavailable."""
    update = _private_update()
    update.effective_message = None
    await journal_bot.show_command(update, _context())


@pytest.mark.asyncio
async def test_show_command_returns_when_chat_id_missing(
    journal_bot: JournalBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Show command should return when chat id cannot be derived."""
    update = _private_update()
    context = _context()
    monkeypatch.setattr(journal_bot, "_chat_id", lambda _update: None)

    await journal_bot.show_command(update, context)

    assert context.bot.send_message.await_count == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_todayinhistory_command_returns_when_message_missing(
    journal_bot: JournalBot,
) -> None:
    """Today-in-history command should no-op when effective_message is missing."""
    update = _private_update()
    update.effective_message = None
    await journal_bot.todayinhistory_command(update, _context())


@pytest.mark.asyncio
async def test_todayinhistory_command_returns_when_chat_id_missing(
    journal_bot: JournalBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Today-in-history should return when chat id cannot be derived."""
    update = _private_update()
    context = _context()
    monkeypatch.setattr(journal_bot, "_chat_id", lambda _update: None)

    await journal_bot.todayinhistory_command(update, context)

    assert context.bot.send_message.await_count == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_attachment_resolution_and_send_fallback_paths(
    journal_bot: JournalBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attachment send helpers should handle missing/outside and send failures."""
    outside = tmp_path.parent / "outside.jpg"
    outside.write_bytes(b"jpg")

    assert journal_bot._resolve_attachment_path("../outside.jpg") is None
    assert journal_bot._resolve_attachment_path("2026/attachments/missing.jpg") is None

    context = _context()
    await journal_bot._send_note_content(
        1,
        context.bot,
        "   \n![[2026/attachments/missing.jpg]]",
    )
    warning_payload = context.bot.send_message.await_args_list[0].args[1]  # type: ignore[attr-defined]
    assert "Attachment not found" in warning_payload

    attachment = tmp_path / "2026" / "attachments" / "ok.jpg"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_bytes(b"jpg")

    def _raise_open(*args: Any, **kwargs: Any) -> Any:
        raise OSError("open failed")

    monkeypatch.setattr(attachment.__class__, "open", _raise_open)
    await journal_bot._send_attachment(1, context.bot, "2026/attachments/ok.jpg")
    failed_payload = context.bot.send_message.await_args_list[-1].args[1]  # type: ignore[attr-defined]
    assert "Failed to send attachment" in failed_payload


@pytest.mark.asyncio
async def test_send_attachment_uses_media_method_by_extension(
    journal_bot: JournalBot,
    tmp_path: Path,
) -> None:
    """Attachment sender should dispatch to media API by file extension."""
    attachments_dir = tmp_path / "2026" / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    photo = attachments_dir / "a.jpg"
    video = attachments_dir / "b.mp4"
    voice = attachments_dir / "c.ogg"
    doc = attachments_dir / "d.bin"
    for path in (photo, video, voice, doc):
        path.write_bytes(b"data")

    context = _context()
    await journal_bot._send_attachment(1, context.bot, "2026/attachments/a.jpg")
    await journal_bot._send_attachment(1, context.bot, "2026/attachments/b.mp4")
    await journal_bot._send_attachment(1, context.bot, "2026/attachments/c.ogg")
    await journal_bot._send_attachment(1, context.bot, "2026/attachments/d.bin")

    assert context.bot.send_photo.await_count == 1  # type: ignore[attr-defined]
    assert context.bot.send_video.await_count == 1  # type: ignore[attr-defined]
    assert context.bot.send_voice.await_count == 1  # type: ignore[attr-defined]
    assert context.bot.send_document.await_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_delete_command_error_paths(journal_bot: JournalBot) -> None:
    """Delete command should validate args and surface missing day/note paths."""
    update = _private_update()

    journal_bot._repository.peek_last_entry = AsyncMock(return_value=None)  # type: ignore
    await journal_bot.delete_command(update, _context())
    assert (
        "No entries to delete" in update.effective_message.reply_text.await_args.args[0]
    )

    await journal_bot.delete_command(update, _context(args=["nope"]))
    assert "Use: /delete" in update.effective_message.reply_text.await_args.args[0]

    await journal_bot.delete_command(update, _context(args=["day", "bad"]))
    assert "Use: /delete day" in update.effective_message.reply_text.await_args.args[0]

    journal_bot._repository.get_note_content = AsyncMock(return_value=None)  # type: ignore
    await journal_bot.delete_command(update, _context(args=["day"]))
    assert "No note found" in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_delete_command_returns_when_message_missing(
    journal_bot: JournalBot,
) -> None:
    """Delete command should no-op when effective_message is unavailable."""
    update = _private_update()
    update.effective_message = None
    await journal_bot.delete_command(update, _context())


@pytest.mark.asyncio
async def test_flush_album_entry_paths(journal_bot: JournalBot) -> None:
    """Album flush should handle missing and valid buffered state."""
    context = _context()

    await journal_bot.flush_album_entry(context)

    context.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "g1"})
    context.application.chat_data = {
        1: {
            ALBUMS_KEY: {
                "g1": {
                    "note_dt": datetime(2026, 3, 7, 18, 34, tzinfo=UTC),
                    "caption": "cap",
                    "images": ["![[a.jpg]]", "![[b.jpg]]"],
                }
            }
        }
    }

    await journal_bot.flush_album_entry(context)
    assert journal_bot._repository.append_entry.await_count >= 1  # type: ignore


@pytest.mark.asyncio
async def test_callback_router_mood_and_tags(journal_bot: JournalBot) -> None:
    """Callback router should update mood and tags frontmatter."""
    context = _context()

    mood_update = _private_update(callback_data=f"{MOOD_CALLBACK_PREFIX}4")
    await journal_bot.callback_router(mood_update, context)
    assert LAST_PROMPT_AT_KEY in context.chat_data

    invalid_mood_update = _private_update(callback_data=f"{MOOD_CALLBACK_PREFIX}x")
    await journal_bot.callback_router(invalid_mood_update, context)

    # SECURITY: Use valid tag from TAG_CHOICES for callback
    tags_update = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}add:hobby")
    await journal_bot.callback_router(tags_update, context)

    remove_update = _private_update(
        callback_data=f"{TAG_CALLBACK_PREFIX}remove:journal"
    )
    await journal_bot.callback_router(remove_update, context)

    assert journal_bot._repository.update_frontmatter.await_count >= 3  # type: ignore
    assert journal_bot._repository.append_entry.await_count >= 1  # type: ignore


@pytest.mark.asyncio
async def test_check_mood_timers_prompt_and_skip(journal_bot: JournalBot) -> None:
    """Mood timer should send prompt only when rule conditions pass."""
    context = _context()
    active = journal_bot._get_active_chats(context)
    active.add(1)

    context.application.chat_data[1] = {}
    await journal_bot.check_mood_timers(context)
    assert context.bot.send_message.await_count == 1  # type: ignore

    context.bot.send_message.reset_mock()  # type: ignore
    journal_bot._repository.note_has_mood = AsyncMock(return_value=True)  # type: ignore
    await journal_bot.check_mood_timers(context)  # type: ignore
    assert context.bot.send_message.await_count == 0  # type: ignore


@pytest.mark.asyncio
async def test_handle_error_and_auth_rejections(journal_bot: JournalBot) -> None:
    """Unauthorized requests should be ignored and errors should be surfaced."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={2},
    )
    update = _private_update(user_id=1)
    context = _context()

    await journal_bot.help_command(update, context)
    assert update.effective_message.reply_text.await_count == 0

    await journal_bot.handle_error(object(), context)

    class _UpdateStub:
        def __init__(self) -> None:
            self.effective_message = SimpleNamespace(reply_text=AsyncMock())

    from telejournal import bot as bot_module

    original_update = bot_module.Update
    bot_module.Update = _UpdateStub  # type: ignore
    try:
        update_stub = _UpdateStub()
        await journal_bot.handle_error(update_stub, context)
        assert update_stub.effective_message.reply_text.await_count == 1  # type: ignore
    finally:
        bot_module.Update = original_update  # type: ignore


def test_keyboards_and_registration(journal_bot: JournalBot) -> None:
    """Keyboard builders and handler registrations should be stable."""
    mood_markup = _mood_keyboard()
    tags_markup = _tags_keyboard({"family"})
    assert mood_markup.inline_keyboard
    assert tags_markup.inline_keyboard
    assert tags_markup.inline_keyboard[0][0].text.startswith("✅")

    handlers: list[object] = []
    errors: list[object] = []

    app = SimpleNamespace(
        add_handler=lambda h: handlers.append(h),
        add_error_handler=lambda h: errors.append(h),
    )
    journal_bot.register_handlers(app)  # type: ignore[arg-type]
    assert handlers
    assert errors

    jq = _FakeJobQueue()
    journal_bot.register_jobs(jq)  # type: ignore[arg-type]
    assert STARTUP_JOB_NAME in jq.once_jobs
    assert jq.once_jobs[STARTUP_JOB_NAME]["when"] == 0
    assert jq.repeat["interval"] == 300


def test_register_jobs_daily_brief_toggle(journal_bot: JournalBot) -> None:
    """Daily brief job should only be scheduled when configured."""
    jq_disabled = _FakeJobQueue()
    journal_bot.register_jobs(jq_disabled)  # type: ignore[arg-type]
    assert DAILY_BRIEF_JOB_NAME not in jq_disabled.daily

    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={1},
        daily_brief_time_utc=datetime.strptime("09:00", "%H:%M").time(),
    )
    jq_enabled = _FakeJobQueue()
    journal_bot.register_jobs(jq_enabled)  # type: ignore[arg-type]
    assert DAILY_BRIEF_JOB_NAME in jq_enabled.daily


@pytest.mark.asyncio
async def test_send_startup_message_notifies_all_configured_chats(
    journal_bot: JournalBot,
) -> None:
    """Startup greeting should be sent to every configured private chat."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={5, 2, 9},
    )
    context = _context()

    await journal_bot.send_startup_message(context)  # type: ignore[arg-type]

    sent_chat_ids = [
        call.args[0] for call in context.bot.send_message.await_args_list  # type: ignore[attr-defined]
    ]
    sent_messages = [
        call.args[1] for call in context.bot.send_message.await_args_list  # type: ignore[attr-defined]
    ]
    assert sent_chat_ids == [2, 5, 9]
    assert sent_messages == [STARTUP_MESSAGE, STARTUP_MESSAGE, STARTUP_MESSAGE]


@pytest.mark.asyncio
async def test_send_startup_message_continues_after_send_failure(
    journal_bot: JournalBot,
) -> None:
    """Startup greeting should continue sending when one chat rejects it."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={1, 2, 3},
    )
    context = _context()
    delivered: list[int] = []

    async def _send_message(chat_id: int, text: str) -> None:
        assert text == STARTUP_MESSAGE
        if chat_id == 2:
            raise OSError("network")
        delivered.append(chat_id)

    context.bot.send_message = AsyncMock(side_effect=_send_message)

    await journal_bot.send_startup_message(context)  # type: ignore[arg-type]

    assert delivered == [1, 3]


@pytest.mark.asyncio
async def test_send_daily_brief_with_historical_notes(
    journal_bot: JournalBot,
) -> None:
    """Daily brief should send summary plus render-choice buttons."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={4},
    )
    journal_bot._repository.get_same_day_previous_year_notes = AsyncMock(  # type: ignore[attr-defined]
        return_value=[
            (datetime(2024, 3, 16, tzinfo=UTC), "note 2024"),
            (datetime(2025, 3, 16, tzinfo=UTC), "note 2025"),
        ]
    )
    context = _context()

    await journal_bot.send_daily_brief(context)  # type: ignore[arg-type]

    assert context.bot.send_message.await_count == 1  # type: ignore[attr-defined]
    payload = context.bot.send_message.await_args_list[0].args[1]  # type: ignore[attr-defined]
    assert "2024" in payload
    assert "2025" in payload
    assert context.bot.send_message.await_args_list[0].kwargs["reply_markup"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_send_daily_brief_without_historical_notes(
    journal_bot: JournalBot,
) -> None:
    """Daily brief should explicitly notify when no same-day memories exist."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={4},
    )
    journal_bot._repository.get_same_day_previous_year_notes = AsyncMock(  # type: ignore[attr-defined]
        return_value=[]
    )
    context = _context()

    await journal_bot.send_daily_brief(context)  # type: ignore[arg-type]

    context.bot.send_message.assert_awaited_once_with(4, NO_MEMORIES_MESSAGE)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_send_daily_brief_continues_after_send_failure(
    journal_bot: JournalBot,
) -> None:
    """Daily brief should continue with remaining users after one send failure."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={1, 2, 3},
    )
    journal_bot._repository.get_same_day_previous_year_notes = AsyncMock(  # type: ignore[attr-defined]
        return_value=[]
    )

    delivered: list[int] = []

    async def _send_message(chat_id: int, text: str) -> None:
        assert text == NO_MEMORIES_MESSAGE
        if chat_id == 2:
            raise OSError("network")
        delivered.append(chat_id)

    context = _context()
    context.bot.send_message = AsyncMock(side_effect=_send_message)

    await journal_bot.send_daily_brief(context)  # type: ignore[arg-type]

    assert delivered == [1, 3]


@pytest.mark.asyncio
async def test_todayinhistory_command_for_requester(
    journal_bot: JournalBot,
) -> None:
    """Manual history command should prompt for mode with available years."""
    update = _private_update()
    context = _context()
    journal_bot._repository.get_same_day_previous_year_notes = AsyncMock(  # type: ignore[attr-defined]
        return_value=[
            (datetime(2024, 3, 16, tzinfo=UTC), "note 2024"),
            (datetime(2025, 3, 16, tzinfo=UTC), "note 2025"),
        ]
    )

    await journal_bot.todayinhistory_command(update, context)  # type: ignore[arg-type]

    assert context.bot.send_message.await_count == 1  # type: ignore[attr-defined]
    payload = context.bot.send_message.await_args_list[0].args[1]  # type: ignore[attr-defined]
    assert "2024" in payload
    assert "2025" in payload
    assert context.bot.send_message.await_args_list[0].kwargs["reply_markup"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_history_callback_raw_and_rendered_modes(
    journal_bot: JournalBot,
    tmp_path: Path,
) -> None:
    """History callback should support both raw and rendered output modes."""
    attachment = tmp_path / "2024" / "attachments" / "mem.jpg"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_bytes(b"jpeg")
    journal_bot._repository.get_same_day_previous_year_notes = AsyncMock(  # type: ignore[attr-defined]
        return_value=[
            (
                datetime(2024, 3, 16, tzinfo=UTC),
                "hello\n![[2024/attachments/mem.jpg]]",
            )
        ]
    )

    raw_context = _context()
    raw_update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}history:raw:2026-03-16"
    )
    await journal_bot.callback_router(raw_update, raw_context)
    raw_payloads = [
        call.args[1] for call in raw_context.bot.send_message.await_args_list  # type: ignore[attr-defined]
    ]
    assert any("![[2024/attachments/mem.jpg]]" in payload for payload in raw_payloads)
    assert raw_context.bot.send_photo.await_count == 0  # type: ignore[attr-defined]

    rendered_context = _context()
    rendered_update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}history:rendered:2026-03-16"
    )
    await journal_bot.callback_router(rendered_update, rendered_context)
    assert rendered_context.bot.send_photo.await_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_reply_quote_adds_source_note_link_when_history_message_known(
    journal_bot: JournalBot,
) -> None:
    """Reply quotes should include a source-note link for tracked history messages."""
    source_note_dt = datetime(2024, 3, 16, tzinfo=UTC)
    context = _context()
    context.chat_data["override_date"] = datetime(2026, 3, 17, tzinfo=UTC)
    journal_bot._reply_source_notes = {1: {55: source_note_dt}}  # type: ignore[attr-defined]

    reply_to_message = SimpleNamespace(
        message_id=55,
        text="On this day in 2024 you wrote...",
        text_markdown_urled="On this day in 2024 you wrote...",
        caption=None,
        from_user=SimpleNamespace(id=999, is_bot=True),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(
        text="my new thought",
        reply_to_message=reply_to_message,
    )

    await journal_bot.handle_journal_entry(update, context)

    entry = journal_bot._repository.append_entry.await_args.args[1]  # type: ignore
    assert "On this day in 2024 you wrote..." in entry
    assert "[Source note](../2024/2024-03-16.md)" in entry


def test_track_reply_source_message_trims_old_entries(journal_bot: JournalBot) -> None:
    """Reply-source map should trim oldest message IDs once max size is exceeded."""
    chat_id = 1
    source_dt = datetime(2024, 3, 16, tzinfo=UTC)

    for message_id in range(MAX_TRACKED_REPLY_SOURCES + 1):
        journal_bot._track_reply_source_message(  # type: ignore[attr-defined]
            chat_id,
            SimpleNamespace(message_id=message_id),
            source_dt,
        )

    tracked = journal_bot._reply_source_notes[chat_id]  # type: ignore[attr-defined]
    assert len(tracked) == 1000
    assert min(tracked) == 1001
    assert max(tracked) == MAX_TRACKED_REPLY_SOURCES


def test_extract_reply_quote_with_source_link_without_quote_text(
    journal_bot: JournalBot,
) -> None:
    """When quoted content is empty, source link should still be returned."""
    source_dt = datetime(2024, 3, 16, tzinfo=UTC)
    note_dt = datetime(2026, 3, 17, tzinfo=UTC)
    journal_bot._reply_source_notes = {1: {99: source_dt}}  # type: ignore[attr-defined]

    message = SimpleNamespace(
        reply_to_message=SimpleNamespace(
            message_id=99,
            text=None,
            caption=None,
            from_user=SimpleNamespace(id=999, is_bot=True),
            photo=None,
            voice=None,
            video=None,
            video_note=None,
            location=None,
        ),
        from_user=SimpleNamespace(id=1, is_bot=False),
    )

    quote = journal_bot._extract_reply_quote_with_source_link(  # type: ignore[attr-defined]
        message,
        1,
        note_dt,
    )
    assert quote == "[Source note](../2024/2024-03-16.md)"


def test_get_active_chats_resets_non_set(journal_bot: JournalBot) -> None:
    """Active chat accessor should normalize invalid bot_data values."""
    context = _context()
    context.application.bot_data[ACTIVE_CHATS_KEY] = "invalid"
    active = journal_bot._get_active_chats(context)
    assert isinstance(active, set)


def test_private_authorization_and_chat_helpers(journal_bot: JournalBot) -> None:
    """Authorization and helper methods should handle invalid chat shapes."""
    context = _context()
    context.chat_data = None
    assert journal_bot._chat_data(context) == {}

    no_chat = SimpleNamespace(effective_chat=None, effective_user=None)
    assert not journal_bot._is_private_and_authorized(no_chat)
    assert journal_bot._chat_id(no_chat) is None

    group = SimpleNamespace(
        effective_chat=SimpleNamespace(type=ChatType.GROUP, id=1),
        effective_user=SimpleNamespace(id=1),
    )
    assert not journal_bot._is_private_and_authorized(group)


@pytest.mark.asyncio
async def test_unauthorized_early_returns_cover_branches(
    journal_bot: JournalBot,
) -> None:
    """Unauthorized updates should early-return across command handlers."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={99},
    )
    update = _private_update(user_id=1)
    context = _context()

    await journal_bot.setdate_command(update, context)
    await journal_bot.resetdate_command(update, context)
    await journal_bot.mood_command(update, context)
    await journal_bot.delete_command(update, context)
    await journal_bot.show_command(update, context)
    await journal_bot.todayinhistory_command(update, context)
    await journal_bot.tags_command(update, context)
    await journal_bot.handle_journal_entry(update, context)

    cb_update = _private_update(callback_data=f"{MOOD_CALLBACK_PREFIX}3")
    await journal_bot.callback_router(cb_update, context)

    assert update.effective_message.reply_text.await_count == 0


@pytest.mark.asyncio
async def test_photo_and_flush_defensive_branches(
    journal_bot: JournalBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Photo and album handlers should tolerate malformed state safely."""
    context = _context()

    update_no_message = SimpleNamespace(effective_message=None, effective_chat=None)
    await journal_bot._handle_photo(
        update_no_message,
        context,
        datetime.now(UTC),
        True,
        1,
    )

    update_no_photo = _private_update(text=None, photo=[])
    await journal_bot._handle_photo(
        update_no_photo,
        context,
        datetime.now(UTC),
        True,
        1,
    )

    update_no_voice = _private_update(text=None, voice=None)
    assert not await journal_bot._handle_voice(
        update_no_voice,
        context,
        datetime.now(UTC),
        True,
        1,
    )

    update_no_video = _private_update(text=None, video=None)
    assert not await journal_bot._handle_video(
        update_no_video,
        context,
        datetime.now(UTC),
        True,
        1,
    )

    update_no_video_note = _private_update(text=None, video_note=None)
    assert not await journal_bot._handle_video_note(
        update_no_video_note,
        context,
        datetime.now(UTC),
        True,
        1,
    )

    class _Photo:
        async def get_file(self) -> object:
            return SimpleNamespace(download_to_drive=AsyncMock())

    monkeypatch.setattr(journal_bot, "_chat_id", lambda _u: None)
    album_update = _private_update(
        text=None,
        caption="album",
        photo=[_Photo()],
        media_group_id="group-x",
    )
    await journal_bot._handle_photo(
        album_update,
        context,
        datetime.now(UTC),
        True,
        1,
    )

    await journal_bot.flush_album_entry(_context())

    bad_job_context = _context()
    bad_job_context.job = SimpleNamespace(data={"chat_id": 1})
    await journal_bot.flush_album_entry(bad_job_context)

    wrong_chat_data = _context()
    wrong_chat_data.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "x"})
    wrong_chat_data.application.chat_data = {1: "bad"}
    await journal_bot.flush_album_entry(wrong_chat_data)

    wrong_album_map = _context()
    wrong_album_map.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "x"})
    wrong_album_map.application.chat_data = {1: {ALBUMS_KEY: "bad"}}
    await journal_bot.flush_album_entry(wrong_album_map)

    missing_album_state = _context()
    missing_album_state.job = SimpleNamespace(
        data={"chat_id": 1, "media_group_id": "missing"}
    )
    missing_album_state.application.chat_data = {1: {ALBUMS_KEY: {}}}
    await journal_bot.flush_album_entry(missing_album_state)

    invalid_note_state = _context()
    invalid_note_state.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "x"})
    invalid_note_state.application.chat_data = {
        1: {ALBUMS_KEY: {"x": {"note_dt": "bad", "images": []}}}
    }
    await journal_bot.flush_album_entry(invalid_note_state)


@pytest.mark.asyncio
async def test_should_include_timestamp_zero_window(journal_bot: JournalBot) -> None:
    """Zero/negative window should always include timestamp and refresh state."""
    journal_bot._settings = Settings(
        telegram_token="token",
        vault_root=journal_bot._settings.vault_root,
        allowed_user_ids={1},
        message_timestamp_window_seconds=0,
    )
    chat_data: dict[str, Any] = {}
    now = datetime.now(UTC)
    include = journal_bot._should_include_timestamp(chat_data, now, now)
    assert include
    assert LAST_WINDOW_AT_KEY in chat_data


@pytest.mark.asyncio
async def test_flush_album_without_timestamp_marker(journal_bot: JournalBot) -> None:
    """Album flush should strip timestamp prefix when grouped messages share window."""
    context = _context()
    context.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "x"})
    context.application.chat_data = {
        1: {
            ALBUMS_KEY: {
                "x": {
                    "note_dt": datetime(2026, 3, 7, 18, 34, tzinfo=UTC),
                    "caption": "caption",
                    "images": ["![[a.jpg]]"],
                    "include_timestamp": False,
                }
            }
        }
    }
    await journal_bot.flush_album_entry(context)
    entry = journal_bot._repository.append_entry.await_args.args[1]  # type: ignore
    assert entry.startswith("caption")


@pytest.mark.asyncio
async def test_callback_router_delete_confirm_and_cancel(
    journal_bot: JournalBot,
) -> None:
    """Delete callbacks should apply confirmed action and support cancel."""
    context = _context()

    cancel_update = _private_update(callback_data=f"{DELETE_CALLBACK_PREFIX}cancel")
    await journal_bot.callback_router(cancel_update, context)
    assert (
        "Deletion canceled"
        in cancel_update.callback_query.edit_message_text.await_args.args[0]
    )

    confirm_last = _private_update(
        callback_data=f"{DELETE_CALLBACK_PREFIX}confirm:last:2026-03-07"
    )
    await journal_bot.callback_router(confirm_last, context)
    assert journal_bot._repository.delete_last_entry.await_count >= 1  # type: ignore

    confirm_day = _private_update(
        callback_data=f"{DELETE_CALLBACK_PREFIX}confirm:day:2026-03-07"
    )
    await journal_bot.callback_router(confirm_day, context)
    assert journal_bot._repository.delete_day.await_count >= 1  # type: ignore


@pytest.mark.asyncio
async def test_callback_router_delete_confirm_error_paths(
    journal_bot: JournalBot,
) -> None:
    """Delete callbacks should ignore invalid payloads and handle missing targets."""
    context = _context()

    invalid_shape = _private_update(
        callback_data=f"{DELETE_CALLBACK_PREFIX}confirm:last"
    )
    await journal_bot.callback_router(invalid_shape, context)

    invalid_date = _private_update(
        callback_data=f"{DELETE_CALLBACK_PREFIX}confirm:last:not-a-date"
    )
    await journal_bot.callback_router(invalid_date, context)

    journal_bot._repository.delete_last_entry = AsyncMock(return_value=None)  # type: ignore
    no_last = _private_update(
        callback_data=f"{DELETE_CALLBACK_PREFIX}confirm:last:2026-03-07"
    )
    await journal_bot.callback_router(no_last, context)
    assert (
        "No entries to delete"
        in no_last.callback_query.edit_message_text.await_args.args[0]
    )

    journal_bot._repository.delete_day = AsyncMock(return_value=False)  # type: ignore
    no_day = _private_update(
        callback_data=f"{DELETE_CALLBACK_PREFIX}confirm:day:2026-03-07"
    )
    await journal_bot.callback_router(no_day, context)
    assert "No note found" in no_day.callback_query.edit_message_text.await_args.args[0]


@pytest.mark.asyncio
async def test_callback_router_mood_change_from_previous(
    journal_bot: JournalBot,
) -> None:
    """Mood callback should record explicit mood change when previous mood exists."""
    journal_bot._repository.get_note_frontmatter = AsyncMock(  # type: ignore
        return_value={"tags": ["journal"], "mood": 3}
    )
    context = _context()
    update = _private_update(callback_data=f"{MOOD_CALLBACK_PREFIX}4")
    await journal_bot.callback_router(update, context)
    entry = journal_bot._repository.append_entry.await_args.args[1]  # type: ignore
    assert "Mood changed" in entry


@pytest.mark.asyncio
async def test_flush_album_logs_oserror(journal_bot: JournalBot) -> None:
    """Album flush should catch storage write OSError and continue."""

    async def _raise(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk")

    journal_bot._record_entry = _raise  # type: ignore[method-assign]
    context = _context()
    context.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "x"})
    context.application.chat_data = {
        1: {
            ALBUMS_KEY: {
                "x": {
                    "note_dt": datetime(2026, 3, 7, 18, 34, tzinfo=UTC),
                    "caption": "",
                    "images": ["![[a.jpg]]"],
                }
            }
        }
    }
    await journal_bot.flush_album_entry(context)


@pytest.mark.asyncio
async def test_handle_journal_entry_defensive_paths(
    journal_bot: JournalBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journal handler should guard missing message/chat and handle OSError."""
    context = _context()
    update = _private_update()

    update.effective_message = None
    await journal_bot.handle_journal_entry(update, context)

    update = _private_update()
    monkeypatch.setattr(journal_bot, "_chat_id", lambda _u: None)
    await journal_bot.handle_journal_entry(update, context)
    monkeypatch.setattr(journal_bot, "_chat_id", lambda _u: 1)

    async def _raise(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk")

    monkeypatch.setattr(journal_bot, "_handle_text", _raise)
    update = _private_update(text="boom")
    await journal_bot.handle_journal_entry(update, context)
    assert update.effective_message.reply_text.await_count == 1


@pytest.mark.asyncio
async def test_callback_router_defensive_paths(journal_bot: JournalBot) -> None:
    """Callback router should reject malformed data and unsupported mood."""
    context = _context()

    update = _private_update()
    update.callback_query = None
    await journal_bot.callback_router(update, context)

    update = _private_update()
    update.callback_query = SimpleNamespace(data=None, answer=AsyncMock())
    await journal_bot.callback_router(update, context)

    bad_mood = _private_update(callback_data=f"{MOOD_CALLBACK_PREFIX}9")
    await journal_bot.callback_router(bad_mood, context)

    bad_history_parts = _private_update(callback_data=f"{HISTORY_CALLBACK_PREFIX}bad")
    await journal_bot.callback_router(bad_history_parts, context)

    bad_history_action = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}invalid:raw:2026-03-06"
    )
    await journal_bot.callback_router(bad_history_action, context)

    bad_history_mode = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}show:invalid:2026-03-06"
    )
    await journal_bot.callback_router(bad_history_mode, context)

    bad_history_date = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}show:raw:not-a-date"
    )
    await journal_bot.callback_router(bad_history_date, context)

    remove_work = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}remove:work")
    await journal_bot.callback_router(remove_work, context)


@pytest.mark.asyncio
async def test_history_callback_show_missing_and_chat_missing(
    journal_bot: JournalBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History callback should handle missing note and missing chat id branches."""
    journal_bot._repository.get_note_content = AsyncMock(return_value=None)  # type: ignore[attr-defined]
    context = _context()
    missing_note_update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}show:raw:2026-03-06"
    )
    await journal_bot.callback_router(missing_note_update, context)
    assert missing_note_update.callback_query.edit_message_text.await_count == 1

    chat_missing_context = _context()
    chat_missing_update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}history:raw:2026-03-06"
    )
    monkeypatch.setattr(journal_bot, "_chat_id", lambda _update: None)
    await journal_bot.callback_router(chat_missing_update, chat_missing_context)
    assert chat_missing_context.bot.send_message.await_count == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_history_callback_no_memories_sends_empty_message(
    journal_bot: JournalBot,
) -> None:
    """History callback should send no-memories message when there is no history."""
    journal_bot._repository.get_same_day_previous_year_notes = AsyncMock(  # type: ignore[attr-defined]
        return_value=[]
    )
    context = _context()
    update = _private_update(
        callback_data=f"{HISTORY_CALLBACK_PREFIX}history:rendered:2026-03-06"
    )
    await journal_bot.callback_router(update, context)

    context.bot.send_message.assert_awaited_with(1, NO_MEMORIES_MESSAGE)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mood_timer_non_dict_chat_data_is_skipped(
    journal_bot: JournalBot,
) -> None:
    """Timer should skip active chat entries with malformed chat_data payload."""
    context = _context()
    journal_bot._get_active_chats(context).add(1)
    context.application.chat_data[1] = "invalid"
    await journal_bot.check_mood_timers(context)
    assert context.bot.send_message.await_count == 0


@pytest.mark.asyncio
async def test_text_message_with_self_reply_quote(journal_bot: JournalBot) -> None:
    """Text message replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="first message",
        text_markdown_urled="first message",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(
        text="this is a great message", reply_to_message=replied_msg
    )
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> first message" in entry_text
    assert "this is a great message" in entry_text


@pytest.mark.asyncio
async def test_photo_message_with_self_reply_quote(journal_bot: JournalBot) -> None:
    """Photo message replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="original text",
        text_markdown_urled="original text",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(
        photo=[object()],
        text=None,
        caption="photo caption",
        reply_to_message=replied_msg,
    )
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> original text" in entry_text
    assert "photo caption" in entry_text


@pytest.mark.asyncio
async def test_voice_message_with_self_reply_quote(journal_bot: JournalBot) -> None:
    """Voice message replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="original",
        text_markdown_urled="original",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(voice=object(), text=None, reply_to_message=replied_msg)
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> original" in entry_text
    assert "Voice recording" in entry_text


@pytest.mark.asyncio
async def test_video_message_with_self_reply_quote(journal_bot: JournalBot) -> None:
    """Video message replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="video reply",
        text_markdown_urled="video reply",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(video=object(), text=None, reply_to_message=replied_msg)
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> video reply" in entry_text


@pytest.mark.asyncio
async def test_location_message_with_self_reply_quote(journal_bot: JournalBot) -> None:
    """Location message replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="meet here",
        text_markdown_urled="meet here",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(
        location=SimpleNamespace(latitude=40.0, longitude=-74.0),
        text=None,
        reply_to_message=replied_msg,
    )
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> meet here" in entry_text
    assert "Location:" in entry_text


@pytest.mark.asyncio
async def test_reply_to_different_user_not_quoted(journal_bot: JournalBot) -> None:
    """Reply to message from different user should not be quoted."""
    replied_msg = SimpleNamespace(
        text="other user message",
        text_markdown_urled="other user message",
        caption=None,
        from_user=SimpleNamespace(id=99),  # Different user
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(text="my reply", reply_to_message=replied_msg)
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> other user message" not in entry_text
    assert "my reply" in entry_text


@pytest.mark.asyncio
async def test_reply_to_media_without_text(journal_bot: JournalBot) -> None:
    """Reply to media message without text should show placeholder."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=[object()],  # Photo without caption
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(text="nice photo", reply_to_message=replied_msg)
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> [Photo]" in entry_text
    assert "nice photo" in entry_text


@pytest.mark.asyncio
async def test_video_note_message_with_self_reply_quote(
    journal_bot: JournalBot,
) -> None:
    """Video note replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="check this",
        text_markdown_urled="check this",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    update = _private_update(
        video_note=object(), text=None, reply_to_message=replied_msg
    )
    context = _context()

    await journal_bot.handle_journal_entry(update, context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> check this" in entry_text
    assert "Video note" in entry_text


@pytest.mark.asyncio
async def test_album_with_self_reply_quote(journal_bot: JournalBot) -> None:
    """Photo album replying to self should include quoted original message."""
    replied_msg = SimpleNamespace(
        text="original album message",
        text_markdown_urled="original album message",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )

    # First photo in album with reply
    update1 = _private_update(
        photo=[object()],
        text=None,
        caption="album caption",
        media_group_id="album123",
        reply_to_message=replied_msg,
    )
    context = _context()
    await journal_bot.handle_journal_entry(update1, context)

    # Second photo in same album
    update2 = _private_update(
        photo=[object()],
        text=None,
        caption=None,
        media_group_id="album123",
        reply_to_message=None,
    )
    await journal_bot.handle_journal_entry(update2, context)

    # Setup context for flush with shared chat_data
    context.job = SimpleNamespace(data={"chat_id": 1, "media_group_id": "album123"})
    context.application.chat_data[1] = context.chat_data

    await journal_bot.flush_album_entry(context)

    assert journal_bot._repository.append_entry.await_count == 1  # type: ignore
    call_args = journal_bot._repository.append_entry.await_args  # type: ignore
    entry_text = call_args.args[1]
    assert "> original album message" in entry_text
    assert "album caption" in entry_text


@pytest.mark.asyncio
async def test_date_bounds_validation(journal_bot: JournalBot) -> None:
    """SECURITY: _parse_iso_date should reject dates outside allowed range."""
    from telejournal.bot import _parse_iso_date

    # Test date too far in past (> 2 years)
    with pytest.raises(ValueError, match="outside allowed range"):
        _parse_iso_date("2020-01-01")

    # Test date too far in future (> 1 year)
    with pytest.raises(ValueError, match="outside allowed range"):
        _parse_iso_date("2030-01-01")


@pytest.mark.asyncio
async def test_callback_router_security_validations(journal_bot: JournalBot) -> None:
    """SECURITY: callback_router should validate and reject invalid tag operations."""
    context = _context()

    # Test invalid tag callback format (missing parts)
    malformed_update = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}badformat")
    await journal_bot.callback_router(malformed_update, context)
    # Should return early without calling repository
    assert journal_bot._repository.update_frontmatter.await_count == 0  # type: ignore

    # Test invalid action (not "add" or "remove")
    invalid_action = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}delete:hobby")
    await journal_bot.callback_router(invalid_action, context)
    assert journal_bot._repository.update_frontmatter.await_count == 0  # type: ignore

    # Test invalid tag (not in TAG_CHOICES and not existing)
    invalid_tag = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}add:malicious")
    await journal_bot.callback_router(invalid_tag, context)
    assert journal_bot._repository.update_frontmatter.await_count == 0  # type: ignore
