"""Behavioral tests for JournalBot handlers and callbacks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatType

from telegram_journal_bot.bot import (
    ACTIVE_CHATS_KEY,
    ALBUMS_KEY,
    LAST_PROMPT_AT_KEY,
    MOOD_CALLBACK_PREFIX,
    TAG_CALLBACK_PREFIX,
    JournalBot,
    _mood_keyboard,
    _tags_keyboard,
)
from telegram_journal_bot.config import Settings


class _FakeJobQueue:
    def __init__(self) -> None:
        self.once_jobs: dict[str, dict[str, Any]] = {}
        self.repeat: dict[str, Any] = {}

    def get_jobs_by_name(self, name: str) -> list[object]:
        if name in self.once_jobs:
            return [object()]
        return []

    def run_once(
        self,
        callback: object,
        *,
        when: int,
        data: dict[str, Any],
        name: str,
    ) -> None:
        self.once_jobs[name] = {
            "callback": callback,
            "when": when,
            "data": data,
            "name": name,
        }

    def run_repeating(self, callback: object, *, interval: int, first: int) -> None:
        self.repeat = {"callback": callback, "interval": interval, "first": first}


@pytest.fixture
def journal_bot(tmp_path: Path) -> JournalBot:
    """Create bot with fake repository methods for handler testing."""
    bot = JournalBot(Settings("token", tmp_path))
    bot._repository = SimpleNamespace(  # type: ignore[assignment]
        append_entry=AsyncMock(),
        delete_last_entry=AsyncMock(return_value="- 18:34:42 > hello"),
        save_photo=AsyncMock(return_value="2026/attachments/ts.jpg"),
        get_note_frontmatter=AsyncMock(
            return_value={"tags": ["journal", "work"], "mood": None}
        ),
        update_frontmatter=AsyncMock(),
        note_has_entry=AsyncMock(return_value=True),
        note_has_mood=AsyncMock(return_value=False),
        get_last_entry_time=AsyncMock(
            return_value=datetime.now(UTC) - timedelta(hours=6)
        ),
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
        bot=SimpleNamespace(send_message=AsyncMock()),
        job=None,
        error=RuntimeError("boom"),
    )


def _private_update(
    *,
    user_id: int = 1,
    text: str | None = "hi",
    caption: str | None = None,
    photo: list[object] | None = None,
    location: object | None = None,
    media_group_id: str | None = None,
    callback_data: str | None = None,
    message_id: int = 1,
) -> SimpleNamespace:
    """Build a minimal private Update-like object."""
    message = SimpleNamespace(
        text=text,
        caption=caption,
        text_markdown_urled=text,
        caption_markdown_urled=caption,
        photo=photo,
        location=location,
        media_group_id=media_group_id,
        message_id=message_id,
        reply_text=AsyncMock(),
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

    assert update.effective_message.reply_text.await_count == 5


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
async def test_entry_ack_and_initial_mood_prompt(journal_bot: JournalBot) -> None:
    """New journal entries should acknowledge write and ask for mood when missing."""
    context = _context()
    update = _private_update(text="hello")

    await journal_bot.handle_journal_entry(update, context)

    replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
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

    replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
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

    replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
    assert "How are you feeling today?" in replies


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

    tags_update = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}add:personal")
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

    from telegram_journal_bot import bot as bot_module

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
    tags_markup = _tags_keyboard({"journal"})
    assert mood_markup.inline_keyboard
    assert tags_markup.inline_keyboard

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
    assert jq.repeat["interval"] == 300


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
    await journal_bot._handle_photo(update_no_message, context, datetime.now(UTC))

    update_no_photo = _private_update(text=None, photo=[])
    await journal_bot._handle_photo(update_no_photo, context, datetime.now(UTC))

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
    await journal_bot._handle_photo(album_update, context, datetime.now(UTC))

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

    remove_work = _private_update(callback_data=f"{TAG_CALLBACK_PREFIX}remove:work")
    await journal_bot.callback_router(remove_work, context)


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
