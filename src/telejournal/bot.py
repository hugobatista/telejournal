"""Telegram bot handlers and application wiring."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    JobQueue,
    MessageHandler,
    filters,
)

from telejournal.bot_callbacks import CallbackRouterService
from telejournal.bot_commands import CommandHandlerService
from telejournal.bot_delivery import NoteDeliveryService
from telejournal.command_registry import visible_command_specs
from telejournal import bot_helpers as _bot_helpers
from telejournal.config import Settings
from telejournal.formatting import (
    build_message_marker,
    extract_reply_quote,
    format_entry_block,
    format_text_entry,
    format_with_quote,
    render_message_markdown,
    wrap_body_with_marker,
)
from telejournal.logic import (
    effective_note_datetime,
    should_prompt_for_mood,
)
from telejournal.bot_media import MediaEntryService
from telejournal.bot_setdate import (
    SETDATE_CALLBACK_PREFIX,
    SetDateFlowService,
)
from telejournal.runtime_config import (
    apply_runtime_setting,
    format_runtime_config_summary,
    persist_runtime_settings,
)
from telejournal.storage import OneDriveAuthorizationRequiredError, build_repository

__all__ = ["JournalBot"]

_chunk_text = _bot_helpers._chunk_text
_parse_iso_date = _bot_helpers._parse_iso_date
_parse_tags_from_args = _bot_helpers._parse_tags_from_args
_mood_keyboard = _bot_helpers._mood_keyboard
_tags_keyboard = _bot_helpers._tags_keyboard
_truncate_message = _bot_helpers._truncate_message
MOOD_CALLBACK_PREFIX = _bot_helpers.MOOD_CALLBACK_PREFIX
TAG_CALLBACK_PREFIX = _bot_helpers.TAG_CALLBACK_PREFIX
DELETE_CALLBACK_PREFIX = _bot_helpers.DELETE_CALLBACK_PREFIX
HISTORY_CALLBACK_PREFIX = _bot_helpers.HISTORY_CALLBACK_PREFIX

LOGGER = logging.getLogger(__name__)

OVERRIDE_DATE_KEY = "override_date"
LAST_ENTRY_AT_KEY = "last_entry_at"
LAST_PROMPT_AT_KEY = "last_prompt_at"
LAST_PROMPT_NOTE_KEY = "last_prompt_note"
ALBUMS_KEY = "albums"
ACTIVE_CHATS_KEY = "active_chats"
LAST_WINDOW_AT_KEY = "last_window_at"
LAST_WINDOW_NOTE_KEY = "last_window_note"
CONFIG_FLOW_KEY = "config_flow"
CONFIG_PENDING_KEY = "config_pending"

CONFIG_CALLBACK_PREFIX = "config:"
ALBUM_JOB_PREFIX = "album-flush"
ALBUM_FLUSH_SECONDS = 2
STARTUP_JOB_NAME = "startup-hello"
STARTUP_MESSAGE = "Hello! Telejournal is starting."
DAILY_BRIEF_JOB_NAME = "daily-brief"
NO_MEMORIES_MESSAGE = "No memories today"
MAX_TRACKED_REPLY_SOURCES = 2000


class JournalBot:
    """Encapsulates handlers and shared state for journal operations."""

    def __init__(self, settings: Settings) -> None:
        """Create bot services from runtime settings."""
        self._settings = settings
        self._repository = build_repository(settings)
        self._reply_source_notes: dict[int, dict[int, datetime]] = {}
        self._note_delivery = NoteDeliveryService(
            repository_provider=lambda: self._repository,
            track_reply_source_message=self._track_reply_source_message,
            no_memories_message=NO_MEMORIES_MESSAGE,
            logger=LOGGER,
        )
        self._media_entries = MediaEntryService(
            repository_provider=lambda: self._repository,
            chat_data_resolver=self._chat_data,
            extract_reply_quote_with_source_link=self._extract_reply_quote_with_source_link,
            record_message_entry=self._record_message_entry,
            record_entry=self._record_entry,
            albums_key=ALBUMS_KEY,
            album_job_prefix=ALBUM_JOB_PREFIX,
            album_flush_seconds=ALBUM_FLUSH_SECONDS,
            logger=LOGGER,
        )
        self._callback_routes = CallbackRouterService(
            repository_provider=lambda: self._repository,
            settings_provider=lambda: self._settings,
            apply_runtime_config=lambda key, value, context: self._apply_runtime_config(
                key,
                value,
                context,
            ),
            config_summary=lambda: self._config_summary(),
            config_keyboard=lambda: self._config_keyboard(),
            config_prompt_bool_keyboard=lambda key: self._config_prompt_bool_keyboard(
                key
            ),
            config_confirm_keyboard=lambda: self._config_confirm_keyboard(),
            chat_id_resolver=lambda update: self._chat_id(update),
            send_note_text_only=lambda *args: self._send_note_text_only(*args),
            send_note_content=lambda *args: self._send_note_content(*args),
            send_historical_notes_for_chat=lambda *args: self._send_historical_notes_for_chat(
                *args
            ),
            should_include_timestamp=lambda chat_data, note_dt, now: (
                self._should_include_timestamp(chat_data, note_dt, now)
            ),
            record_entry=lambda *args, **kwargs: self._record_entry(*args, **kwargs),
            config_callback_prefix=CONFIG_CALLBACK_PREFIX,
            config_flow_key=CONFIG_FLOW_KEY,
            config_pending_key=CONFIG_PENDING_KEY,
            last_prompt_at_key=LAST_PROMPT_AT_KEY,
            last_prompt_note_key=LAST_PROMPT_NOTE_KEY,
            logger=LOGGER,
        )
        self._commands = CommandHandlerService(
            repository_provider=lambda: self._repository,
            settings_provider=lambda: self._settings,
            is_private_and_authorized=lambda update: self._is_private_and_authorized(
                update
            ),
            chat_data_resolver=lambda context: self._chat_data(context),
            config_summary=lambda: self._config_summary(),
            config_keyboard=lambda: self._config_keyboard(),
            safe_user_error=lambda update, message: self._safe_user_error(
                update, message
            ),
            override_date_key=OVERRIDE_DATE_KEY,
            logger=LOGGER,
        )
        self._setdate_flow = SetDateFlowService(
            is_private_and_authorized=lambda update: self._is_private_and_authorized(
                update
            ),
            chat_data_resolver=lambda context: self._chat_data(context),
            setdate_with_args=lambda update, context: self._commands.setdate_command(
                update,
                context,
            ),
            override_date_key=OVERRIDE_DATE_KEY,
        )

    def _config_summary(self) -> str:
        """Render current runtime-configurable values for /settings."""
        return format_runtime_config_summary(self._settings)

    def _config_keyboard(self) -> InlineKeyboardMarkup:
        """Build keyboard for interactive /settings setting selection."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "tag_choices",
                        callback_data=f"{CONFIG_CALLBACK_PREFIX}edit:tag_choices",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "daily_brief_time_utc",
                        callback_data=(
                            f"{CONFIG_CALLBACK_PREFIX}edit:daily_brief_time_utc"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "prompt_for_mood_if_missing",
                        callback_data=(
                            f"{CONFIG_CALLBACK_PREFIX}edit:"
                            "prompt_for_mood_if_missing"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "bot_menu_enabled",
                        callback_data=(
                            f"{CONFIG_CALLBACK_PREFIX}edit:bot_menu_enabled"
                        ),
                    )
                ],
            ]
        )

    @staticmethod
    def _config_confirm_keyboard() -> InlineKeyboardMarkup:
        """Build keyboard to confirm or cancel pending config update."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Apply",
                        callback_data=f"{CONFIG_CALLBACK_PREFIX}confirm",
                    ),
                    InlineKeyboardButton(
                        "Cancel",
                        callback_data=f"{CONFIG_CALLBACK_PREFIX}cancel",
                    ),
                ]
            ]
        )

    @staticmethod
    def _config_prompt_bool_keyboard(key: str) -> InlineKeyboardMarkup:
        """Build keyboard for boolean runtime configuration values."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "true",
                        callback_data=(f"{CONFIG_CALLBACK_PREFIX}set_bool:{key}:true"),
                    ),
                    InlineKeyboardButton(
                        "false",
                        callback_data=(f"{CONFIG_CALLBACK_PREFIX}set_bool:{key}:false"),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Back",
                        callback_data=f"{CONFIG_CALLBACK_PREFIX}back",
                    )
                ],
            ]
        )

    @staticmethod
    def _note_relpath(note_dt: datetime) -> str:
        """Return normalized note path as ``YYYY/YYYY-MM-DD.md``."""
        return f"{note_dt.year}/{note_dt.strftime('%Y-%m-%d')}.md"

    @classmethod
    def _build_source_note_link(
        cls, source_note_dt: datetime, note_dt: datetime
    ) -> str:
        """Build markdown link from current note directory to source note path."""
        source_relpath = cls._note_relpath(source_note_dt)
        current_dir = str(note_dt.year)
        relative_path = os.path.relpath(source_relpath, start=current_dir)
        relative_path = relative_path.replace(os.sep, "/")
        return f"[Source note]({relative_path})"

    def _track_reply_source_message(
        self,
        chat_id: int,
        sent_message: Any,
        source_note_dt: datetime,
    ) -> None:
        """Remember which Telegram message ids correspond to historical notes."""
        message_id = getattr(sent_message, "message_id", None)
        if not isinstance(message_id, int):
            return

        tracked = self._reply_source_notes.setdefault(chat_id, {})
        tracked[message_id] = source_note_dt

        if len(tracked) <= MAX_TRACKED_REPLY_SOURCES:
            return

        for stale_message_id in sorted(tracked)[: len(tracked) - 1000]:
            tracked.pop(stale_message_id, None)

    def _extract_reply_quote_with_source_link(
        self,
        message: Any,
        chat_id: int,
        note_dt: datetime,
    ) -> str | None:
        """Extract reply quote and append source-note link when available."""
        quote = extract_reply_quote(message)

        reply_to = getattr(message, "reply_to_message", None)
        reply_message_id_raw = getattr(reply_to, "message_id", None)
        if not isinstance(reply_message_id_raw, int):
            return quote
        reply_message_id: int = reply_message_id_raw

        source_note_dt = self._reply_source_notes.get(chat_id, {}).get(reply_message_id)
        if source_note_dt is None:
            return quote

        source_link = self._build_source_note_link(source_note_dt, note_dt)
        if quote:
            return f"{quote}\n\n{source_link}"
        return source_link

    @staticmethod
    def _chat_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
        """Return mutable per-chat state, normalizing absent values to a dict."""
        chat_data = context.chat_data
        if isinstance(chat_data, dict):
            return chat_data
        return {}

    def _is_private_and_authorized(self, update: Update) -> bool:
        """Validate private chat type and user whitelist."""
        if not update.effective_chat:
            return False
        if update.effective_chat.type != ChatType.PRIVATE:
            return False

        user = update.effective_user
        return bool(user and user.id in self._settings.allowed_user_ids)

    @staticmethod
    def _get_active_chats(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
        """Return mutable set of active chat IDs from bot data."""
        active_chats = context.application.bot_data.setdefault(ACTIVE_CHATS_KEY, set())
        if isinstance(active_chats, set):
            return cast(set[int], active_chats)
        new_chats: set[int] = set()
        context.application.bot_data[ACTIVE_CHATS_KEY] = new_chats
        return new_chats

    @staticmethod
    def _chat_id(update: Update) -> int | None:
        """Return effective chat id if present."""
        if not update.effective_chat:
            return None
        return update.effective_chat.id

    def _reschedule_daily_brief(
        self,
        job_queue: JobQueue[Any] | None,
        daily_brief_time_utc: time | None,
    ) -> None:
        """Reschedule the daily brief job after runtime config updates."""
        if job_queue is None:
            return

        for job in job_queue.get_jobs_by_name(DAILY_BRIEF_JOB_NAME):
            schedule_removal = getattr(job, "schedule_removal", None)
            if callable(schedule_removal):
                schedule_removal()

        if daily_brief_time_utc is None:
            return

        job_queue.run_daily(
            self.send_daily_brief,
            time=daily_brief_time_utc,
            name=DAILY_BRIEF_JOB_NAME,
        )

    def _apply_runtime_config(
        self,
        key: str,
        value: Any,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> str:
        """Apply one runtime config change and return a user confirmation."""
        updated_settings, message = apply_runtime_setting(self._settings, key, value)
        persisted_settings, target_path, backup_path = persist_runtime_settings(
            updated_settings
        )
        self._settings = persisted_settings

        if key == "daily_brief_time_utc":
            self._reschedule_daily_brief(
                context.job_queue, self._settings.daily_brief_time_utc
            )

        if backup_path is not None:
            return f"{message}\n" f"Saved to {target_path}\n" f"Backup: {backup_path}"
        return f"{message}\nSaved to {target_path}"

    async def _maybe_handle_config_text_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        message: Any,
    ) -> bool:
        """Handle guided /settings text input; returns True when consumed."""
        chat_data = self._chat_data(context)
        flow_state = chat_data.get(CONFIG_FLOW_KEY)
        if not isinstance(flow_state, dict):
            return False

        if flow_state.get("state") != "await_input":
            return False

        field = flow_state.get("field")
        if not isinstance(field, str):
            return False
        if not isinstance(message.text, str):
            await message.reply_text("Please send plain text for this setting.")
            return True

        raw_text = message.text.strip()
        if field == "tag_choices":
            from telejournal.config import _parse_tag_choices

            try:
                parsed = _parse_tag_choices(raw_text)
            except ValueError:
                await message.reply_text(
                    "Invalid value. Send tags as CSV using lowercase letters, "
                    "numbers, underscore, or hyphen. Example: family,health,love"
                )
                return True

            chat_data[CONFIG_PENDING_KEY] = {
                "key": field,
                "value": list(parsed),
            }
            chat_data[CONFIG_FLOW_KEY] = {"state": "await_confirm"}
            await message.reply_text(
                f"Apply tag_choices = {', '.join(parsed)}?",
                reply_markup=self._config_confirm_keyboard(),
            )
            return True

        if field == "daily_brief_time_utc":
            from telejournal.config import _parse_daily_brief_time_utc

            try:
                parsed_time = _parse_daily_brief_time_utc(raw_text)
            except ValueError:
                await message.reply_text(
                    "Invalid value. Use 0, HH:MM, or HH:MM:SS in UTC. " "Example: 09:00"
                )
                return True

            serialized = "0"
            if parsed_time is not None:
                serialized = parsed_time.strftime("%H:%M:%S")

            chat_data[CONFIG_PENDING_KEY] = {
                "key": field,
                "value": serialized,
            }
            chat_data[CONFIG_FLOW_KEY] = {"state": "await_confirm"}
            await message.reply_text(
                f"Apply daily_brief_time_utc = {serialized}?",
                reply_markup=self._config_confirm_keyboard(),
            )
            return True

        await message.reply_text("This setting expects button input. Use /settings.")
        return True

    async def _safe_user_error(
        self,
        update: Update,
        message: str,
    ) -> None:
        """Reply with a user-facing error when possible."""
        if update.effective_message:
            await update.effective_message.reply_text(message)

    def _resolve_attachment_path(self, attachment_rel: str) -> Path | None:
        """Resolve a relative attachment path under vault root safely."""
        return self._note_delivery.resolve_attachment_path(attachment_rel)

    async def _send_chunked_text(
        self,
        chat_id: int,
        bot: Any,
        text: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send text using Telegram-safe chunk sizes."""
        await self._note_delivery.send_chunked_text(
            chat_id,
            bot,
            text,
            source_note_dt=source_note_dt,
        )

    async def _send_attachment(
        self,
        chat_id: int,
        bot: Any,
        attachment_rel: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send one attachment based on file extension with graceful fallback."""
        await self._note_delivery.send_attachment(
            chat_id,
            bot,
            attachment_rel,
            source_note_dt=source_note_dt,
        )

    async def _send_note_payload(
        self,
        chat_id: int,
        bot: Any,
        payload: Any,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send parsed note chunks as text and media in source order."""
        await self._note_delivery.send_note_payload(
            chat_id,
            bot,
            payload,
            source_note_dt=source_note_dt,
        )

    async def _send_note_content(
        self,
        chat_id: int,
        bot: Any,
        note_content: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Parse note content and send it to a Telegram chat."""
        await self._note_delivery.send_note_content(
            chat_id,
            bot,
            note_content,
            source_note_dt=source_note_dt,
        )

    async def _send_note_text_only(
        self,
        chat_id: int,
        bot: Any,
        note_content: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send note content exactly as text, preserving embed links."""
        await self._note_delivery.send_note_text_only(
            chat_id,
            bot,
            note_content,
            source_note_dt=source_note_dt,
        )

    async def _send_historical_notes_for_chat(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
        render_mode: str,
    ) -> None:
        """Send historical notes in selected mode for one chat."""
        await self._note_delivery.send_historical_notes_for_chat(
            chat_id,
            bot,
            reference_dt,
            render_mode,
        )

    async def _send_history_brief_prompt(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
    ) -> None:
        """Send brief summary of available years and ask for output format."""
        await self._note_delivery.send_history_brief_prompt(
            chat_id,
            bot,
            reference_dt,
        )

    async def help_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send command usage information."""
        await self._commands.help_command(update, context)

    async def setdate_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Set date override directly or start the guided /setdate flow."""
        await self._setdate_flow.start(update, context)

    async def setdate_calendar_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        """Handle setdate calendar callbacks for month navigation and date pick."""
        return await self._setdate_flow.handle_calendar_callback(update, context)

    async def setdate_conversation_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        """Handle guided /setdate date input from chat text."""
        return await self._setdate_flow.handle_text_input(update, context)

    async def resetdate_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Reset date override from in-memory chat state."""
        await self._commands.resetdate_command(update, context)

    async def delete_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Delete the last entry or a full day note."""
        await self._commands.delete_command(update, context)

    async def show_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display current effective day note or a specific YYYY-MM-DD note."""
        await self._commands.show_command(update, context)

    async def mood_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display inline mood selector."""
        await self._commands.mood_command(update, context)

    async def tags_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display tags keyboard or add tags directly from command args."""
        await self._commands.tags_command(update, context)

    async def settings_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Start guided runtime configuration for supported settings."""
        await self._commands.settings_command(update, context)

    async def onedriveauth_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Run OneDrive authorization workflow using device-code flow."""
        await self._commands.onedriveauth_command(update, context)

    async def _record_entry(
        self,
        chat_data: dict[str, Any],
        note_dt: datetime,
        entry: str,
        *,
        frontmatter_updates: dict[str, Any] | None = None,
        as_continuation: bool = False,
    ) -> None:
        """Persist entry and update in-memory tracking timestamps."""
        await self._repository.append_entry(
            note_dt,
            entry,
            frontmatter_updates=frontmatter_updates,
            as_continuation=as_continuation,
        )
        chat_data[LAST_ENTRY_AT_KEY] = datetime.now(UTC)

    async def _record_message_entry(
        self,
        chat_data: dict[str, Any],
        note_dt: datetime,
        body: str,
        include_timestamp: bool,
        message_marker: str,
        *,
        frontmatter_updates: dict[str, Any] | None = None,
        as_continuation: bool = False,
    ) -> None:
        """Persist one message payload wrapped with a stable message marker."""
        marked_body = wrap_body_with_marker(body, message_marker)
        entry = format_entry_block(note_dt, marked_body, include_timestamp)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            frontmatter_updates=frontmatter_updates,
            as_continuation=as_continuation,
        )

    async def _update_message_entry(
        self,
        note_dt: datetime,
        message_marker: str,
        body: str,
        *,
        frontmatter_updates: dict[str, Any] | None = None,
    ) -> bool:
        """Update one existing message payload by its stable marker."""
        return await self._repository.update_marked_entry(
            note_dt,
            message_marker,
            body,
            frontmatter_updates=frontmatter_updates,
        )

    def _should_include_timestamp(
        self,
        chat_data: dict[str, Any],
        note_dt: datetime,
        now: datetime,
    ) -> bool:
        """Return whether current entry should include a fresh timestamp."""
        window_seconds = self._settings.message_timestamp_window_seconds
        if window_seconds <= 0:
            chat_data[LAST_WINDOW_AT_KEY] = now
            chat_data[LAST_WINDOW_NOTE_KEY] = note_dt.strftime("%Y-%m-%d")
            return True

        note_key = note_dt.strftime("%Y-%m-%d")
        last_note_key = chat_data.get(LAST_WINDOW_NOTE_KEY)
        last_window_at = chat_data.get(LAST_WINDOW_AT_KEY)

        if last_note_key != note_key or not isinstance(last_window_at, datetime):
            chat_data[LAST_WINDOW_AT_KEY] = now
            chat_data[LAST_WINDOW_NOTE_KEY] = note_key
            return True

        if (now - last_window_at).total_seconds() > window_seconds:
            chat_data[LAST_WINDOW_AT_KEY] = now
            return True

        chat_data[LAST_WINDOW_AT_KEY] = now
        return False

    async def _handle_photo(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a photo and append embed entry in note."""
        return await self._media_entries.handle_photo(
            update,
            context,
            note_dt,
            include_timestamp,
            chat_id,
            message_marker,
            flush_album_callback=self.flush_album_entry,
        )

    async def flush_album_entry(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Flush a buffered album to a single note entry."""
        await self._media_entries.flush_album_entry(context)

    async def _handle_location(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        latitude: float,
        longitude: float,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> None:
        """Persist a location message as a markdown journal line."""
        await self._media_entries.handle_location(
            update,
            context,
            note_dt,
            latitude,
            longitude,
            include_timestamp,
            chat_id,
            message_marker,
        )

    async def _handle_text(
        self,
        message_text: str,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        message_marker: str,
        quote: str | None = None,
    ) -> None:
        """Persist a text message as a journal line."""
        await self._media_entries.handle_text(
            message_text,
            context,
            note_dt,
            include_timestamp,
            message_marker,
            quote=quote,
        )

    async def _handle_voice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a voice recording and append embed entry in note."""
        return await self._media_entries.handle_voice(
            update,
            context,
            note_dt,
            include_timestamp,
            chat_id,
            message_marker,
        )

    async def _handle_video(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a video message and append embed entry in note."""
        return await self._media_entries.handle_video(
            update,
            context,
            note_dt,
            include_timestamp,
            chat_id,
            message_marker,
        )

    async def _handle_video_note(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a video note (circular video) and append embed entry in note."""
        return await self._media_entries.handle_video_note(
            update,
            context,
            note_dt,
            include_timestamp,
            chat_id,
            message_marker,
        )

    async def _prompt_for_mood_if_missing(
        self,
        message: Any,
        chat_data: dict[str, Any],
        note_dt: datetime,
        now: datetime,
    ) -> None:
        """Prompt for mood when note has entries and still no mood for the day."""
        if not self._settings.prompt_for_mood_if_missing:
            return

        note_key = note_dt.strftime("%Y-%m-%d")
        last_prompted_at = chat_data.get(LAST_PROMPT_AT_KEY)
        if chat_data.get(LAST_PROMPT_NOTE_KEY) != note_key:
            last_prompted_at = None

        note_has_entry = await self._repository.note_has_entry(note_dt)
        note_has_mood = await self._repository.note_has_mood(note_dt)
        should_prompt = should_prompt_for_mood(
            note_has_entry=note_has_entry,
            note_has_mood=note_has_mood,
            now=now,
            last_prompted_at=last_prompted_at,
            reminder_interval_hours=4,
        )
        if not should_prompt:
            return

        await message.reply_text(
            "How are you feeling today?",
            reply_markup=_mood_keyboard(),
        )
        chat_data[LAST_PROMPT_AT_KEY] = now
        chat_data[LAST_PROMPT_NOTE_KEY] = note_key

    async def handle_journal_entry(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle incoming private text/photo/location journal events."""
        if not self._is_private_and_authorized(update):
            return

        message = update.effective_message
        if not message:
            return

        chat_id = self._chat_id(update)
        if chat_id is None:
            return
        self._get_active_chats(context).add(chat_id)

        now = datetime.now(UTC)
        chat_data = self._chat_data(context)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            now,
        )
        if message.text:
            handled_config = await self._maybe_handle_config_text_input(
                update,
                context,
                message,
            )
            if handled_config:
                return

        is_edited_message = getattr(update, "edited_message", None) is not None
        message_id_raw = getattr(message, "message_id", None)
        if not isinstance(message_id_raw, int):
            return
        message_marker = build_message_marker(chat_id, message_id_raw)

        if is_edited_message:
            if not message.text:
                await message.reply_text(
                    "Edited media messages are ignored. "
                    "Edited text messages are updated in-place."
                )
                return

            text = render_message_markdown(message)
            quote = self._extract_reply_quote_with_source_link(
                message,
                chat_id,
                note_dt,
            )
            body = format_text_entry(text)
            if quote:
                body = format_with_quote(quote, body)

            try:
                updated = await self._update_message_entry(
                    note_dt,
                    message_marker,
                    body,
                )
            except OSError:
                LOGGER.exception("Vault write failed")
                await self._safe_user_error(
                    update,
                    "❌ Vault write failed. Check VAULT_ROOT permissions.",
                )
                return

            if updated:
                await message.reply_text("✅ Edited entry updated in journal.")
            else:
                await message.reply_text(
                    "⚠️ Could not locate original entry to edit. "
                    "Only newer entries are editable."
                )
            return

        wrote_entry = False
        include_timestamp = self._should_include_timestamp(chat_data, note_dt, now)

        try:
            if message.photo:
                wrote_entry = await self._handle_photo(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                    chat_id,
                    message_marker,
                )
            elif message.voice:
                wrote_entry = await self._handle_voice(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                    chat_id,
                    message_marker,
                )
            elif message.video:
                wrote_entry = await self._handle_video(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                    chat_id,
                    message_marker,
                )
            elif message.video_note:
                wrote_entry = await self._handle_video_note(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                    chat_id,
                    message_marker,
                )
            elif message.location:
                await self._handle_location(
                    update,
                    context,
                    note_dt,
                    message.location.latitude,
                    message.location.longitude,
                    include_timestamp,
                    chat_id,
                    message_marker,
                )
                wrote_entry = True
            elif message.text:
                text = render_message_markdown(message)
                quote = self._extract_reply_quote_with_source_link(
                    message,
                    chat_id,
                    note_dt,
                )
                await self._handle_text(
                    text,
                    context,
                    note_dt,
                    include_timestamp,
                    message_marker,
                    quote,
                )
                wrote_entry = True
        except OSError:
            LOGGER.exception("Vault write failed")
            await self._safe_user_error(
                update,
                "❌ Vault write failed. Check VAULT_ROOT permissions.",
            )
            return

        if wrote_entry:
            await message.reply_text("✅ Added to journal.")
            await self._prompt_for_mood_if_missing(message, chat_data, note_dt, now)

        LOGGER.info(
            "Entry written for chat_id=%s date=%s",
            chat_id,
            note_dt.strftime("%Y-%m-%d"),
        )

    async def callback_router(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle mood and tag callback interactions."""
        # SECURITY: Authorization check must be first, before any processing
        if not self._is_private_and_authorized(update):
            return

        query = update.callback_query
        if not query or not query.data:
            return

        if query.data.startswith(SETDATE_CALLBACK_PREFIX):
            await self.setdate_calendar_callback(update, context)
            return

        await query.answer()

        chat_data = self._chat_data(context)
        now = datetime.now(UTC)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            now,
        )

        await self._callback_routes.route(
            update=update,
            context=context,
            chat_data=chat_data,
            note_dt=note_dt,
            now=now,
        )

    async def check_mood_timers(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Periodic task to prompt mood when today's note still has no mood."""
        if not self._settings.prompt_for_mood_if_missing:
            return

        now = datetime.now(UTC)
        active_chats = self._get_active_chats(context)
        for chat_id in list(active_chats):
            raw_chat_data = context.application.chat_data.get(chat_id)
            if not isinstance(raw_chat_data, dict):
                continue
            chat_data = raw_chat_data

            note_dt = effective_note_datetime(chat_data.get(OVERRIDE_DATE_KEY), now)
            note_key = note_dt.strftime("%Y-%m-%d")
            note_has_entry = await self._repository.note_has_entry(note_dt)
            note_has_mood = await self._repository.note_has_mood(note_dt)

            last_prompted_at = chat_data.get(LAST_PROMPT_AT_KEY)
            if chat_data.get(LAST_PROMPT_NOTE_KEY) != note_key:
                last_prompted_at = None

            should_prompt = should_prompt_for_mood(
                note_has_entry=note_has_entry,
                note_has_mood=note_has_mood,
                now=now,
                last_prompted_at=last_prompted_at,
                reminder_interval_hours=4,
            )
            if not should_prompt:
                continue

            await context.bot.send_message(
                chat_id,
                "How's your mood today?",
                reply_markup=_mood_keyboard(),
            )
            chat_data[LAST_PROMPT_AT_KEY] = now
            chat_data[LAST_PROMPT_NOTE_KEY] = note_key

    async def send_startup_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send a startup greeting to each configured private chat."""
        startup_message = STARTUP_MESSAGE
        auth_instructions_builder = getattr(
            self._repository,
            "build_authorization_instructions",
            None,
        )
        if callable(auth_instructions_builder):
            try:
                instructions = auth_instructions_builder()
            except RuntimeError:
                LOGGER.exception("Failed to build OneDrive startup auth instructions")
            else:
                if instructions:
                    startup_message = f"{STARTUP_MESSAGE}\n\n{instructions}"

        for chat_id in sorted(self._settings.allowed_user_ids):
            try:
                await context.bot.send_message(chat_id, startup_message)
            except (OSError, TelegramError):
                LOGGER.exception(
                    "Failed to send startup greeting to chat_id=%s",
                    chat_id,
                )

    async def _send_daily_brief_for_chat(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
    ) -> None:
        """Send daily brief summary and prompt for render mode selection."""
        await self._send_history_brief_prompt(chat_id, bot, reference_dt)

    async def todayinhistory_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send same-day historical notes from previous years to the requester."""
        if not self._is_private_and_authorized(update):
            return
        if not update.effective_message:
            return

        chat_id = self._chat_id(update)
        if chat_id is None:
            return
        await self._send_history_brief_prompt(chat_id, context.bot, datetime.now(UTC))

    async def send_daily_brief(
        self,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send same-day historical notes from previous years to all users."""
        for chat_id in sorted(self._settings.allowed_user_ids):
            try:
                await self._send_daily_brief_for_chat(
                    chat_id,
                    context.bot,
                    datetime.now(UTC),
                )
            except (OSError, TelegramError):
                LOGGER.exception(
                    "Failed to send daily brief to chat_id=%s",
                    chat_id,
                )

    async def handle_error(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Log unexpected handler errors and return a generic user message."""
        if isinstance(context.error, OneDriveAuthorizationRequiredError):
            if isinstance(update, Update) and update.effective_message:
                await update.effective_message.reply_text(str(context.error))
            return

        LOGGER.exception(
            "Unhandled exception while processing update", exc_info=context.error
        )

        if not isinstance(update, Update):
            return
        if update.effective_message:
            await update.effective_message.reply_text(
                "❌ Something went wrong while processing that message."
            )

    def register_handlers(self, application: Application) -> None:  # type: ignore[type-arg]
        """Register bot command, callback, and message handlers."""
        text_filter = filters.TEXT & (~filters.COMMAND) & filters.ChatType.PRIVATE
        photo_filter = filters.PHOTO & filters.ChatType.PRIVATE
        voice_filter = filters.VOICE & filters.ChatType.PRIVATE
        video_filter = filters.VIDEO & filters.ChatType.PRIVATE
        video_note_filter = filters.VIDEO_NOTE & filters.ChatType.PRIVATE
        location_filter = filters.LOCATION & filters.ChatType.PRIVATE
        edited_text_filter = (
            filters.UpdateType.EDITED_MESSAGE
            & filters.TEXT
            & (~filters.COMMAND)
            & filters.ChatType.PRIVATE
        )

        storage_provider = self._settings.storage_provider
        command_specs = visible_command_specs(storage_provider)
        for spec in command_specs:
            callback = getattr(self, spec.callback_name)
            application.add_handler(CommandHandler(spec.command, callback))

        application.add_handler(MessageHandler(text_filter, self.handle_journal_entry))
        application.add_handler(MessageHandler(photo_filter, self.handle_journal_entry))
        application.add_handler(MessageHandler(voice_filter, self.handle_journal_entry))
        application.add_handler(MessageHandler(video_filter, self.handle_journal_entry))
        application.add_handler(
            MessageHandler(video_note_filter, self.handle_journal_entry)
        )
        application.add_handler(
            MessageHandler(location_filter, self.handle_journal_entry)
        )
        application.add_handler(
            MessageHandler(edited_text_filter, self.handle_journal_entry)
        )

        application.add_handler(CallbackQueryHandler(self.callback_router))
        application.add_error_handler(self.handle_error)

    def register_jobs(self, job_queue: JobQueue) -> None:  # type: ignore[type-arg]
        """Register periodic reminder jobs."""
        job_queue.run_once(self.send_startup_message, when=0, name=STARTUP_JOB_NAME)
        job_queue.run_repeating(self.check_mood_timers, interval=300, first=300)
        if self._settings.daily_brief_time_utc is not None:
            job_queue.run_daily(
                self.send_daily_brief,
                time=self._settings.daily_brief_time_utc,
                name=DAILY_BRIEF_JOB_NAME,
            )

    async def shutdown(self) -> None:
        """Best-effort shutdown hook used to flush pending repository writes."""
        flush_pending = getattr(self._repository, "flush_pending", None)
        if not callable(flush_pending):
            return

        try:
            await flush_pending(reason="shutdown")
        except Exception:
            LOGGER.exception("Failed to flush pending storage writes during shutdown")
