"""Telegram bot handlers and application wiring."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
    Job,
    JobQueue,
    MessageHandler,
    filters,
)

from telejournal.config import Settings
from telejournal.formatting import (
    AttachmentChunk,
    MOOD_LABELS,
    NoteRenderPayload,
    TextChunk,
    extract_mood_value,
    extract_reply_quote,
    format_album_entry,
    format_entry_block,
    format_location_entry,
    format_mood_change_text,
    format_mood_saved_text,
    format_photo_entry,
    format_text_entry,
    format_with_quote,
    parse_note_render_payload,
    render_message_markdown,
)
from telejournal.logic import (
    effective_note_datetime,
    parse_setdate_args,
    should_prompt_for_mood,
)
from telejournal.storage import VaultRepository

__all__ = ["JournalBot"]

LOGGER = logging.getLogger(__name__)

OVERRIDE_DATE_KEY = "override_date"
LAST_ENTRY_AT_KEY = "last_entry_at"
LAST_PROMPT_AT_KEY = "last_prompt_at"
LAST_PROMPT_NOTE_KEY = "last_prompt_note"
ALBUMS_KEY = "albums"
ACTIVE_CHATS_KEY = "active_chats"
LAST_WINDOW_AT_KEY = "last_window_at"
LAST_WINDOW_NOTE_KEY = "last_window_note"

MOOD_CALLBACK_PREFIX = "mood:"
TAG_CALLBACK_PREFIX = "tag:"
DELETE_CALLBACK_PREFIX = "delete:"
HISTORY_CALLBACK_PREFIX = "history:"
ALBUM_JOB_PREFIX = "album-flush"
ALBUM_FLUSH_SECONDS = 2
STARTUP_JOB_NAME = "startup-hello"
STARTUP_MESSAGE = "Hello! Telejournal is starting."
DAILY_BRIEF_JOB_NAME = "daily-brief"
NO_MEMORIES_MESSAGE = "No memories today"

TAG_CHOICES = ["family", "health", "love", "hobby", "other", "finance", "social"]
MAX_TELEGRAM_TEXT_LEN = 4096
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
VOICE_EXTENSIONS = {".ogg", ".opus"}


def _parse_tags_from_args(args: list[str]) -> set[str]:
    """Parse tag names from /tags command args, supporting commas and spaces."""
    parsed: set[str] = set()
    for raw in args:
        for part in raw.split(","):
            tag = part.strip().lower()
            if tag:
                parsed.add(tag)
    return parsed


def _parse_iso_date(raw_date: str) -> datetime:
    """Parse YYYY-MM-DD into a UTC datetime at midnight.

    Validates that the date is within reasonable bounds:
    - Not more than 2 years in the past
    - Not more than 1 year in the future
    """
    parsed_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    result = datetime.combine(parsed_date, datetime.min.time()).replace(tzinfo=UTC)

    # SECURITY: Validate date bounds to prevent DoS via extreme dates
    now = datetime.now(UTC)
    min_date = now - timedelta(days=730)  # 2 years back
    max_date = now + timedelta(days=365)  # 1 year forward

    if result < min_date or result > max_date:
        raise ValueError(
            f"Date {raw_date} is outside allowed range "
            f"({min_date.date()} to {max_date.date()})"
        )

    return result


def _truncate_message(text: str, max_len: int = MAX_TELEGRAM_TEXT_LEN) -> str:
    """Trim long output to fit Telegram message size limits."""
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 5]}\n..."


def _chunk_text(text: str, chunk_size: int = MAX_TELEGRAM_TEXT_LEN) -> list[str]:
    """Split long text into Telegram-sized chunks, preferring line boundaries."""
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > chunk_size:
        split_at = remaining.rfind("\n", 0, chunk_size)
        if split_at <= 0:
            split_at = chunk_size

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:chunk_size]
            split_at = len(chunk)
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)
    return chunks


def _mood_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for mood selection."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{MOOD_CALLBACK_PREFIX}{value}")
        for value, label in MOOD_LABELS.items()
    ]
    return InlineKeyboardMarkup([buttons])


def _tags_keyboard(current_tags: set[str]) -> InlineKeyboardMarkup:
    """Build inline keyboard for tags add/remove interactions."""
    buttons: list[list[InlineKeyboardButton]] = []
    for tag in TAG_CHOICES:
        if tag in current_tags:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"✅ {tag}",
                        callback_data=f"{TAG_CALLBACK_PREFIX}remove:{tag}",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"➕ {tag}",
                        callback_data=f"{TAG_CALLBACK_PREFIX}add:{tag}",
                    )
                ]
            )
    return InlineKeyboardMarkup(buttons)


def _delete_confirmation_keyboard(action: str, note_date: str) -> InlineKeyboardMarkup:
    """Build confirmation keyboard for delete actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Confirm",
                    callback_data=(
                        f"{DELETE_CALLBACK_PREFIX}confirm:{action}:{note_date}"
                    ),
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"{DELETE_CALLBACK_PREFIX}cancel",
                ),
            ]
        ]
    )


def _history_render_keyboard(action: str, date_str: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for choosing note-only vs rendered output."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Notes only",
                    callback_data=(f"{HISTORY_CALLBACK_PREFIX}{action}:raw:{date_str}"),
                ),
                InlineKeyboardButton(
                    "Rendered",
                    callback_data=(
                        f"{HISTORY_CALLBACK_PREFIX}{action}:rendered:{date_str}"
                    ),
                ),
            ]
        ]
    )


class JournalBot:
    """Encapsulates handlers and shared state for journal operations."""

    def __init__(self, settings: Settings) -> None:
        """Create bot services from runtime settings."""
        self._settings = settings
        self._repository = VaultRepository(
            settings.vault_root,
            secure_permissions=settings.secure_file_permissions,
        )

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
        candidate = (self._repository.vault_root / attachment_rel).resolve()
        vault_root = self._repository.vault_root.resolve()

        if candidate != vault_root and vault_root not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    async def _send_chunked_text(
        self,
        chat_id: int,
        bot: Any,
        text: str,
    ) -> None:
        """Send text using Telegram-safe chunk sizes."""
        if not text or not text.strip():
            return
        for payload in _chunk_text(text):
            await bot.send_message(chat_id, payload)

    async def _send_attachment(
        self,
        chat_id: int,
        bot: Any,
        attachment_rel: str,
    ) -> None:
        """Send one attachment based on file extension with graceful fallback."""
        attachment_path = self._resolve_attachment_path(attachment_rel)
        if attachment_path is None:
            await bot.send_message(
                chat_id,
                f"⚠️ Attachment not found: {attachment_rel}",
            )
            return

        suffix = attachment_path.suffix.lower()
        try:
            with attachment_path.open("rb") as attachment_file:
                if suffix in PHOTO_EXTENSIONS:
                    await bot.send_photo(chat_id, attachment_file)
                elif suffix in VIDEO_EXTENSIONS:
                    await bot.send_video(chat_id, attachment_file)
                elif suffix in VOICE_EXTENSIONS:
                    await bot.send_voice(chat_id, attachment_file)
                else:
                    await bot.send_document(chat_id, attachment_file)
        except (OSError, TelegramError):
            LOGGER.exception("Failed to send attachment %s", attachment_rel)
            await bot.send_message(
                chat_id,
                f"⚠️ Failed to send attachment: {attachment_rel}",
            )

    async def _send_note_payload(
        self,
        chat_id: int,
        bot: Any,
        payload: NoteRenderPayload,
    ) -> None:
        """Send parsed note chunks as text and media in source order."""
        for chunk in payload.chunks:
            if isinstance(chunk, TextChunk):
                await self._send_chunked_text(chat_id, bot, chunk.text)
            elif isinstance(chunk, AttachmentChunk):
                await self._send_attachment(chat_id, bot, chunk.attachment_rel)

    async def _send_note_content(
        self,
        chat_id: int,
        bot: Any,
        note_content: str,
    ) -> None:
        """Parse note content and send it to a Telegram chat."""
        payload = parse_note_render_payload(note_content)
        await self._send_note_payload(chat_id, bot, payload)

    async def _send_note_text_only(
        self,
        chat_id: int,
        bot: Any,
        note_content: str,
    ) -> None:
        """Send note content exactly as text, preserving embed links."""
        await self._send_chunked_text(chat_id, bot, note_content)

    async def _send_historical_notes_for_chat(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
        render_mode: str,
    ) -> None:
        """Send historical notes in selected mode for one chat."""
        historical_notes = await self._repository.get_same_day_previous_year_notes(
            reference_dt
        )

        if not historical_notes:
            await bot.send_message(chat_id, NO_MEMORIES_MESSAGE)
            return

        date_label = reference_dt.strftime("%m-%d")
        await bot.send_message(chat_id, f"📅 On this day ({date_label})")
        for note_dt, content in historical_notes:
            await bot.send_message(
                chat_id,
                f"==== {note_dt.strftime('%Y-%m-%d')} ====",
            )
            if render_mode == "raw":
                await self._send_note_text_only(chat_id, bot, content)
            else:
                await self._send_note_content(chat_id, bot, content)

    async def _send_history_brief_prompt(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
    ) -> None:
        """Send brief summary of available years and ask for output format."""
        historical_notes = await self._repository.get_same_day_previous_year_notes(
            reference_dt
        )

        if not historical_notes:
            await bot.send_message(chat_id, NO_MEMORIES_MESSAGE)
            return

        years = ", ".join(str(note_dt.year) for note_dt, _ in historical_notes)
        date_str = reference_dt.strftime("%Y-%m-%d")
        date_label = reference_dt.strftime("%m-%d")
        await bot.send_message(
            chat_id,
            (
                f"📅 On this day ({date_label}) I found notes for: {years}.\n"
                "How do you want to view them?"
            ),
            reply_markup=_history_render_keyboard("history", date_str),
        )

    async def help_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send command usage information."""
        del context
        if not self._is_private_and_authorized(update):
            return

        help_text = (
            "📝 Telejournal Bot Usage\n\n"
            "• Every private message is journaled\n"
            "• Photos are embedded from attachments/\n"
            "• Voice recordings are embedded from attachments/\n"
            "• Video messages (including circular video notes) are embedded from attachments/\n"
            "• Messages within the configured time window share one timestamp\n"
            "• Mood tracked via /mood (😢 😐 😌 🙂 😊)\n\n"
            "Commands:\n"
            "/setdate YYYY-MM-DD [HH:MM:SS]  Set target note date/time\n"
            "/resetdate  Return to today\n"
            "/tags  Show tag buttons\n"
            "/tags work kids  Add/select one or more tags\n"
            "/mood  Open mood picker\n"
            "/show  Show current effective day note\n"
            "/show YYYY-MM-DD  Show a specific day note\n"
            "/todayinhistory  Show same-day notes from previous years\n"
            "/delete  Delete last entry and show deleted content\n"
            "/delete day [YYYY-MM-DD]  Delete full day note\n"
            "/help"
        )

        if update.effective_message:
            await update.effective_message.reply_text(help_text)

    async def setdate_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Set in-memory date override for subsequent entries."""
        if not self._is_private_and_authorized(update):
            return

        now = datetime.now(UTC)
        args = context.args or []
        try:
            override_dt = parse_setdate_args(args, now)
        except ValueError:
            LOGGER.warning("Invalid /setdate input: %s", args)
            await self._safe_user_error(
                update,
                "❌ Use: /setdate YYYY-MM-DD [HH:MM:SS]",
            )
            return

        chat_data = self._chat_data(context)
        chat_data[OVERRIDE_DATE_KEY] = override_dt
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Date override set to {override_dt.strftime('%Y-%m-%d')} (UTC)."
            )

    async def resetdate_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Reset date override from in-memory chat state."""
        if not self._is_private_and_authorized(update):
            return

        chat_data = self._chat_data(context)
        chat_data.pop(OVERRIDE_DATE_KEY, None)
        if update.effective_message:
            await update.effective_message.reply_text("Date override reset.")

    async def delete_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Delete the last entry or a full day note."""
        if not self._is_private_and_authorized(update):
            return

        chat_data = self._chat_data(context)
        default_note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            datetime.now(UTC),
        )

        if not update.effective_message:
            return

        args = context.args or []
        if not args:
            preview = await self._repository.peek_last_entry(default_note_dt)
            if preview is None:
                await update.effective_message.reply_text("No entries to delete.")
                return

            preview_text = _truncate_message(preview, max_len=3600)
            await update.effective_message.reply_text(
                f"Confirm deleting the last entry?\n\n{preview_text}",
                reply_markup=_delete_confirmation_keyboard(
                    "last",
                    default_note_dt.strftime("%Y-%m-%d"),
                ),
            )
            return

        if args[0].lower() != "day" or len(args) > 2:
            await update.effective_message.reply_text(
                "❌ Use: /delete OR /delete day [YYYY-MM-DD]"
            )
            return

        note_dt = default_note_dt
        if len(args) == 2:
            try:
                note_dt = _parse_iso_date(args[1])
            except ValueError:
                await update.effective_message.reply_text(
                    "❌ Use: /delete day [YYYY-MM-DD]"
                )
                return

        note_content = await self._repository.get_note_content(note_dt)
        if note_content is None:
            await update.effective_message.reply_text(
                f"No note found for {note_dt.strftime('%Y-%m-%d')}."
            )
            return

        await update.effective_message.reply_text(
            f"Confirm deleting day {note_dt.strftime('%Y-%m-%d')}?",
            reply_markup=_delete_confirmation_keyboard(
                "day",
                note_dt.strftime("%Y-%m-%d"),
            ),
        )

    async def show_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display current effective day note or a specific YYYY-MM-DD note."""
        if not self._is_private_and_authorized(update):
            return

        if not update.effective_message:
            return

        chat_data = self._chat_data(context)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            datetime.now(UTC),
        )

        args = context.args or []
        if args:
            if len(args) != 1:
                await update.effective_message.reply_text("❌ Use: /show [YYYY-MM-DD]")
                return
            try:
                note_dt = _parse_iso_date(args[0])
            except ValueError:
                await update.effective_message.reply_text("❌ Use: /show [YYYY-MM-DD]")
                return

        note_content = await self._repository.get_note_content(note_dt)
        if note_content is None:
            await update.effective_message.reply_text(
                f"No note found for {note_dt.strftime('%Y-%m-%d')}."
            )
            return

        await update.effective_message.reply_text(
            f"How do you want to view note {note_dt.strftime('%Y-%m-%d')}?",
            reply_markup=_history_render_keyboard(
                "show",
                note_dt.strftime("%Y-%m-%d"),
            ),
        )

    async def mood_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display inline mood selector."""
        del context
        if not self._is_private_and_authorized(update):
            return

        if update.effective_message:
            await update.effective_message.reply_text(
                "How are you feeling today?",
                reply_markup=_mood_keyboard(),
            )

    async def tags_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display tags keyboard or add tags directly from command args."""
        if not self._is_private_and_authorized(update):
            return

        chat_data = self._chat_data(context)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            datetime.now(UTC),
        )
        frontmatter = await self._repository.get_note_frontmatter(note_dt)
        current_tags = set(frontmatter.get("tags") or ["journal"])

        args = context.args or []
        if args:
            parsed_tags = _parse_tags_from_args(args)
            if parsed_tags:
                current_tags.update(parsed_tags)
                await self._repository.update_frontmatter(
                    note_dt,
                    {"tags": sorted(current_tags)},
                )

        rendered_tags = ", ".join(sorted(current_tags))
        if update.effective_message:
            response = (
                f"Updated: {rendered_tags}" if args else f"Current: {rendered_tags}"
            )
            await update.effective_message.reply_text(
                response,
                reply_markup=_tags_keyboard(current_tags),
            )

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
    ) -> bool:
        """Persist a photo and append embed entry in note."""
        message = update.effective_message
        if not message or not message.photo:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        best_photo = message.photo[-1]
        attachment_rel = await self._repository.save_photo(best_photo, note_dt, ts)
        caption = render_message_markdown(message)

        media_group_id = message.media_group_id
        if media_group_id and context.job_queue is not None:
            chat_data = self._chat_data(context)
            albums = chat_data.setdefault(ALBUMS_KEY, {})

            # Extract quote only once per album (from first message)
            quote = None
            if media_group_id not in albums:
                quote = extract_reply_quote(message)

            album_state = albums.setdefault(
                media_group_id,
                {
                    "note_dt": note_dt,
                    "caption": "",
                    "images": [],
                    "include_timestamp": include_timestamp,
                    "quote": quote,
                },
            )

            if caption and not album_state.get("caption"):
                album_state["caption"] = caption
            album_state.setdefault("images", []).append(f"![[{attachment_rel}]]")

            chat_id = self._chat_id(update)
            if chat_id is None:
                return False
            job_name = f"{ALBUM_JOB_PREFIX}:{chat_id}:{media_group_id}"
            if context.job_queue is not None:
                if not context.job_queue.get_jobs_by_name(job_name):
                    context.job_queue.run_once(
                        self.flush_album_entry,
                        when=ALBUM_FLUSH_SECONDS,
                        data={"chat_id": chat_id, "media_group_id": media_group_id},
                        name=job_name,
                    )
            return False

        heading = format_photo_entry(caption, attachment_rel, "Photo")

        quote = extract_reply_quote(message)
        if quote:
            heading = format_with_quote(quote, heading)

        entry = format_entry_block(note_dt, heading, include_timestamp)

        chat_data = self._chat_data(context)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            as_continuation=not include_timestamp,
        )
        return True

    async def flush_album_entry(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Flush a buffered album to a single note entry."""
        job: Job[Any] | None = context.job
        if job is None or not isinstance(job.data, dict):
            return

        chat_id = job.data.get("chat_id")
        media_group_id = job.data.get("media_group_id")
        if chat_id is None or media_group_id is None:
            return

        chat_data = context.application.chat_data.get(chat_id)
        if not isinstance(chat_data, dict):
            return

        albums = chat_data.get(ALBUMS_KEY)
        if not isinstance(albums, dict):
            return

        album_state = albums.pop(media_group_id, None)
        if not isinstance(album_state, dict):
            return

        note_dt = album_state.get("note_dt")
        images = album_state.get("images") or []
        caption = album_state.get("caption") or ""
        quote = album_state.get("quote")
        if not isinstance(note_dt, datetime) or not images:
            return

        heading = format_album_entry(caption, images, "Photo album")

        if quote:
            heading = format_with_quote(quote, heading)

        include_timestamp = bool(album_state.get("include_timestamp", True))
        entry = format_entry_block(note_dt, heading, include_timestamp)
        try:
            await self._record_entry(
                chat_data,
                note_dt,
                entry,
                as_continuation=not include_timestamp,
            )
            await context.bot.send_message(chat_id, "✅ Added to journal.")
        except OSError:
            LOGGER.exception(
                "Vault write failed while flushing album for chat_id=%s",
                chat_id,
            )

    async def _handle_location(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        latitude: float,
        longitude: float,
        include_timestamp: bool,
    ) -> None:
        """Persist a location message as a markdown journal line."""
        body = format_location_entry(latitude, longitude)

        message = update.effective_message
        if message:
            quote = extract_reply_quote(message)
            if quote:
                body = format_with_quote(quote, body)

        entry = format_entry_block(note_dt, body, include_timestamp)

        location_data = {
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
        }
        chat_data = self._chat_data(context)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            frontmatter_updates={"location": location_data},
            as_continuation=not include_timestamp,
        )

    async def _handle_text(
        self,
        message_text: str,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        quote: str | None = None,
    ) -> None:
        """Persist a text message as a journal line."""
        body = format_text_entry(message_text)
        if quote:
            body = format_with_quote(quote, body)
        entry = format_entry_block(note_dt, body, include_timestamp)
        chat_data = self._chat_data(context)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            as_continuation=not include_timestamp,
        )

    async def _handle_voice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
    ) -> bool:
        """Persist a voice recording and append embed entry in note."""
        message = update.effective_message
        if not message or not message.voice:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        attachment_rel = await self._repository.save_voice(message.voice, note_dt, ts)
        caption = render_message_markdown(message)
        body = format_photo_entry(caption, attachment_rel, "Voice recording")

        quote = extract_reply_quote(message)
        if quote:
            body = format_with_quote(quote, body)

        entry = format_entry_block(note_dt, body, include_timestamp)

        chat_data = self._chat_data(context)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            as_continuation=not include_timestamp,
        )
        return True

    async def _handle_video(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
    ) -> bool:
        """Persist a video message and append embed entry in note."""
        message = update.effective_message
        if not message or not message.video:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        attachment_rel = await self._repository.save_video(message.video, note_dt, ts)
        caption = render_message_markdown(message)
        body = format_photo_entry(caption, attachment_rel, "Video message")

        quote = extract_reply_quote(message)
        if quote:
            body = format_with_quote(quote, body)

        entry = format_entry_block(note_dt, body, include_timestamp)

        chat_data = self._chat_data(context)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            as_continuation=not include_timestamp,
        )
        return True

    async def _handle_video_note(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
    ) -> bool:
        """Persist a video note (circular video) and append embed entry in note."""
        message = update.effective_message
        if not message or not message.video_note:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        attachment_rel = await self._repository.save_video_note(
            message.video_note, note_dt, ts
        )
        caption = render_message_markdown(message)
        body = format_photo_entry(caption, attachment_rel, "Video note")

        quote = extract_reply_quote(message)
        if quote:
            body = format_with_quote(quote, body)

        entry = format_entry_block(note_dt, body, include_timestamp)

        chat_data = self._chat_data(context)
        await self._record_entry(
            chat_data,
            note_dt,
            entry,
            as_continuation=not include_timestamp,
        )
        return True

    async def _prompt_for_mood_if_missing(
        self,
        message: Any,
        chat_data: dict[str, Any],
        note_dt: datetime,
        now: datetime,
    ) -> None:
        """Prompt for mood when note has entries and still no mood for the day."""
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
        wrote_entry = False
        include_timestamp = self._should_include_timestamp(chat_data, note_dt, now)

        try:
            if message.photo:
                wrote_entry = await self._handle_photo(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                )
            elif message.voice:
                wrote_entry = await self._handle_voice(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                )
            elif message.video:
                wrote_entry = await self._handle_video(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                )
            elif message.video_note:
                wrote_entry = await self._handle_video_note(
                    update,
                    context,
                    note_dt,
                    include_timestamp,
                )
            elif message.location:
                await self._handle_location(
                    update,
                    context,
                    note_dt,
                    message.location.latitude,
                    message.location.longitude,
                    include_timestamp,
                )
                wrote_entry = True
            elif message.text:
                text = render_message_markdown(message)
                quote = extract_reply_quote(message)
                await self._handle_text(
                    text, context, note_dt, include_timestamp, quote
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

        await query.answer()

        chat_data = self._chat_data(context)
        now = datetime.now(UTC)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            now,
        )

        if query.data.startswith(HISTORY_CALLBACK_PREFIX):
            parts = query.data.split(":")
            if len(parts) != 4:
                return

            _, action, render_mode, raw_date = parts
            if action not in {"show", "history"}:
                return
            if render_mode not in {"raw", "rendered"}:
                return

            try:
                target_dt = _parse_iso_date(raw_date)
            except ValueError:
                return

            chat_id = self._chat_id(update)
            if chat_id is None:
                return

            if action == "show":
                note_content = await self._repository.get_note_content(target_dt)
                if note_content is None:
                    await query.edit_message_text(
                        f"No note found for {target_dt.strftime('%Y-%m-%d')}."
                    )
                    return

                await query.edit_message_text("Sending note...")
                if render_mode == "raw":
                    await self._send_note_text_only(chat_id, context.bot, note_content)
                else:
                    await self._send_note_content(chat_id, context.bot, note_content)
                return

            await query.edit_message_text("Sending memories...")
            await self._send_historical_notes_for_chat(
                chat_id,
                context.bot,
                target_dt,
                render_mode,
            )
            return

        if query.data.startswith(MOOD_CALLBACK_PREFIX):
            raw_value = query.data.removeprefix(MOOD_CALLBACK_PREFIX)
            try:
                mood = int(raw_value)
            except ValueError:
                return

            if mood not in MOOD_LABELS:
                return

            frontmatter = await self._repository.get_note_frontmatter(note_dt)
            previous = extract_mood_value(frontmatter.get("mood"))

            await self._repository.update_frontmatter(note_dt, {"mood": mood})

            if previous != mood:
                change_text = format_mood_change_text(previous, mood)

                include_timestamp = self._should_include_timestamp(
                    chat_data,
                    note_dt,
                    now,
                )
                await self._record_entry(
                    chat_data,
                    note_dt,
                    format_entry_block(
                        note_dt,
                        format_text_entry(change_text),
                        include_timestamp,
                    ),
                    as_continuation=not include_timestamp,
                )

            chat_data[LAST_PROMPT_AT_KEY] = now
            chat_data[LAST_PROMPT_NOTE_KEY] = note_dt.strftime("%Y-%m-%d")
            await query.edit_message_text(format_mood_saved_text(mood))
            return

        if query.data.startswith(DELETE_CALLBACK_PREFIX):
            if query.data == f"{DELETE_CALLBACK_PREFIX}cancel":
                await query.edit_message_text("Deletion canceled.")
                return

            parts = query.data.split(":")
            if len(parts) != 4 or parts[1] != "confirm":
                return

            action = parts[2]
            try:
                target_note_dt = _parse_iso_date(parts[3])
            except ValueError:
                return

            if action == "last":
                deleted = await self._repository.delete_last_entry(target_note_dt)
                if deleted is None:
                    await query.edit_message_text("No entries to delete.")
                    return
                deleted_preview = _truncate_message(deleted, max_len=3600)
                await query.edit_message_text(
                    f"🗑️ Deleted last entry:\n\n{deleted_preview}"
                )
                return

            if action == "day":
                deleted_day = await self._repository.delete_day(target_note_dt)
                if not deleted_day:
                    await query.edit_message_text(
                        f"No note found for {target_note_dt.strftime('%Y-%m-%d')}."
                    )
                    return
                await query.edit_message_text(
                    f"🗑️ Deleted day {target_note_dt.strftime('%Y-%m-%d')}."
                )
                return

        if query.data.startswith(TAG_CALLBACK_PREFIX):
            # SECURITY: Validate callback data to prevent injection
            try:
                _, action, tag = query.data.split(":", maxsplit=2)
            except ValueError:
                LOGGER.warning("Invalid tag callback data format: %s", query.data)
                return

            # SECURITY: Whitelist validation for action
            if action not in ("add", "remove"):
                LOGGER.warning("Invalid tag action: %s", action)
                return

            frontmatter = await self._repository.get_note_frontmatter(note_dt)
            current_tags = set(frontmatter.get("tags") or ["journal"])

            # SECURITY: Accept tags from TAG_CHOICES or existing tags
            # This allows removal of tags added via /tags command
            if tag not in TAG_CHOICES and tag not in current_tags:
                LOGGER.warning(
                    "Invalid tag value (not in choices or existing): %s", tag
                )
                return

            if action == "add":
                current_tags.add(tag)
            elif action == "remove" and tag != "journal":
                current_tags.discard(tag)

            updates = {"tags": sorted(current_tags)}
            await self._repository.update_frontmatter(note_dt, updates)

            rendered_tags = ", ".join(sorted(current_tags))
            await query.edit_message_text(
                f"Current: {rendered_tags}",
                reply_markup=_tags_keyboard(current_tags),
            )

    async def check_mood_timers(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Periodic task to prompt mood when today's note still has no mood."""
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
        for chat_id in sorted(self._settings.allowed_user_ids):
            try:
                await context.bot.send_message(chat_id, STARTUP_MESSAGE)
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

        application.add_handler(CommandHandler("setdate", self.setdate_command))
        application.add_handler(CommandHandler("resetdate", self.resetdate_command))
        application.add_handler(CommandHandler("tags", self.tags_command))
        application.add_handler(CommandHandler("mood", self.mood_command))
        application.add_handler(CommandHandler("delete", self.delete_command))
        application.add_handler(CommandHandler("show", self.show_command))
        application.add_handler(
            CommandHandler("todayinhistory", self.todayinhistory_command)
        )
        application.add_handler(CommandHandler("help", self.help_command))

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
