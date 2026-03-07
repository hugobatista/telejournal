"""Telegram bot handlers and application wiring."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
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

from telegram_journal_bot.config import Settings
from telegram_journal_bot.formatting import (
    format_location_entry,
    format_text_entry,
    render_message_markdown,
)
from telegram_journal_bot.logic import (
    effective_note_datetime,
    parse_setdate_args,
    should_prompt_for_mood,
)
from telegram_journal_bot.storage import VaultRepository

__all__ = ["JournalBot", "Update"]

LOGGER = logging.getLogger(__name__)

OVERRIDE_DATE_KEY = "override_date"
LAST_ENTRY_AT_KEY = "last_entry_at"
LAST_PROMPT_AT_KEY = "last_prompt_at"
ALBUMS_KEY = "albums"
ACTIVE_CHATS_KEY = "active_chats"

MOOD_CALLBACK_PREFIX = "mood:"
TAG_CALLBACK_PREFIX = "tag:"
ALBUM_JOB_PREFIX = "album-flush"
ALBUM_FLUSH_SECONDS = 2

MOOD_LABELS = {
    1: "😢",
    2: "😐",
    3: "😌",
    4: "🙂",
    5: "😊",
}

TAG_CHOICES = [ "personal", "family", "health", "love", "hobby", "other", "finance", "social"]


def _mood_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for mood selection."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{MOOD_CALLBACK_PREFIX}{value}")
        for value, label in MOOD_LABELS.items()
    ]
    return InlineKeyboardMarkup([buttons])


def _tags_keyboard(current_tags: set[str]) -> InlineKeyboardMarkup:
    """Build inline keyboard for tags add/remove interactions."""
    buttons: list[InlineKeyboardButton] = []
    for tag in TAG_CHOICES:
        if tag in current_tags:
            buttons.append(
                InlineKeyboardButton(
                    f"-{tag}",
                    callback_data=f"{TAG_CALLBACK_PREFIX}remove:{tag}",
                )
            )
        else:
            buttons.append(
                InlineKeyboardButton(
                    f"+{tag}",
                    callback_data=f"{TAG_CALLBACK_PREFIX}add:{tag}",
                )
            )
    return InlineKeyboardMarkup([buttons])


class JournalBot:
    """Encapsulates handlers and shared state for journal operations."""

    def __init__(self, settings: Settings) -> None:
        """Create bot services from runtime settings."""
        self._settings = settings
        self._repository = VaultRepository(settings.vault_root)

    @staticmethod
    def _chat_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
        """Return mutable per-chat state, normalizing absent values to a dict."""
        chat_data = context.chat_data
        if isinstance(chat_data, dict):
            return chat_data
        return {}

    def _is_private_and_authorized(self, update: Update) -> bool:
        """Validate private chat type and optional user whitelist."""
        if not update.effective_chat:
            return False
        if update.effective_chat.type != ChatType.PRIVATE:
            return False

        if not self._settings.allowed_user_ids:
            return True

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
            "📝 Journal Bot Usage\n\n"
            "• Every message becomes a journal entry\n"
            "• Photos -> embedded in attachments/ folder\n"
            "• Locations -> coordinates + Google Maps link\n"
            "• Mood tracked via /mood (😢 😐 😌 🙂 😊)\n\n"
            "/setdate 2026-03-07 [HH:MM:SS]\n"
            "/resetdate\n"
            "/tags\n"
            "/mood\n"
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
        """Display current tags and inline controls to mutate tags."""
        if not self._is_private_and_authorized(update):
            return

        chat_data = self._chat_data(context)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            datetime.now(UTC),
        )
        frontmatter = await self._repository.get_note_frontmatter(note_dt)
        current_tags = set(frontmatter.get("tags") or ["journal"])

        rendered_tags = ", ".join(sorted(current_tags))
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Current: {rendered_tags}",
                reply_markup=_tags_keyboard(current_tags),
            )

    async def _record_entry(
        self,
        chat_data: dict[str, Any],
        note_dt: datetime,
        entry: str,
    ) -> None:
        """Persist entry and update in-memory tracking timestamps."""
        await self._repository.append_entry(note_dt, entry)
        chat_data[LAST_ENTRY_AT_KEY] = datetime.now(UTC)

    async def _handle_photo(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
    ) -> None:
        """Persist a photo and append embed entry in note."""
        message = update.effective_message
        if not message or not message.photo:
            return

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        best_photo = message.photo[-1]
        attachment_rel = await self._repository.save_photo(best_photo, note_dt, ts)
        caption = render_message_markdown(message)

        media_group_id = message.media_group_id
        if media_group_id and context.job_queue is not None:
            chat_data = self._chat_data(context)
            albums = chat_data.setdefault(ALBUMS_KEY, {})
            album_state = albums.setdefault(
                media_group_id,
                {
                    "note_dt": note_dt,
                    "caption": "",
                    "images": [],
                },
            )

            if caption and not album_state.get("caption"):
                album_state["caption"] = caption
            album_state.setdefault("images", []).append(f"![[{attachment_rel}]]")

            chat_id = self._chat_id(update)
            if chat_id is None:
                return
            job_name = f"{ALBUM_JOB_PREFIX}:{chat_id}:{media_group_id}"
            if context.job_queue is not None:
                if not context.job_queue.get_jobs_by_name(job_name):
                    context.job_queue.run_once(
                        self.flush_album_entry,
                        when=ALBUM_FLUSH_SECONDS,
                        data={"chat_id": chat_id, "media_group_id": media_group_id},
                        name=job_name,
                    )
            return

        heading = (
            format_text_entry(note_dt, caption)
            if caption
            else note_dt.strftime("%H:%M")
        )
        entry = f"{heading}\n![[{attachment_rel}]]"

        chat_data = self._chat_data(context)
        await self._record_entry(chat_data, note_dt, entry)

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
        if not isinstance(note_dt, datetime) or not images:
            return

        heading = (
            format_text_entry(note_dt, caption)
            if caption
            else note_dt.strftime("%H:%M")
        )
        entry = "\n".join([heading, *images])
        try:
            await self._record_entry(chat_data, note_dt, entry)
        except OSError:
            LOGGER.exception(
                "Vault write failed while flushing album for chat_id=%s",
                chat_id,
            )

    async def _handle_location(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        latitude: float,
        longitude: float,
    ) -> None:
        """Persist a location message as a markdown journal line."""
        entry = format_location_entry(note_dt, latitude, longitude)
        chat_data = self._chat_data(context)
        await self._record_entry(chat_data, note_dt, entry)

    async def _handle_text(
        self,
        message_text: str,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
    ) -> None:
        """Persist a text message as a journal line."""
        entry = format_text_entry(note_dt, message_text)
        chat_data = self._chat_data(context)
        await self._record_entry(chat_data, note_dt, entry)

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

        try:
            if message.photo:
                await self._handle_photo(update, context, note_dt)
            elif message.location:
                await self._handle_location(
                    context,
                    note_dt,
                    message.location.latitude,
                    message.location.longitude,
                )
            elif message.text:
                text = render_message_markdown(message)
                await self._handle_text(text, context, note_dt)
        except OSError:
            LOGGER.exception("Vault write failed")
            await self._safe_user_error(
                update,
                "❌ Vault write failed. Check VAULT_ROOT permissions.",
            )
            return

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
        if not self._is_private_and_authorized(update):
            return

        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()

        chat_data = self._chat_data(context)
        note_dt = effective_note_datetime(
            chat_data.get(OVERRIDE_DATE_KEY),
            datetime.now(UTC),
        )

        if query.data.startswith(MOOD_CALLBACK_PREFIX):
            raw_value = query.data.removeprefix(MOOD_CALLBACK_PREFIX)
            try:
                mood = int(raw_value)
            except ValueError:
                return

            if mood not in MOOD_LABELS:
                return
            await self._repository.update_frontmatter(note_dt, {"mood": mood})
            chat_data[LAST_PROMPT_AT_KEY] = datetime.now(UTC)
            await query.edit_message_text(
                f"Mood saved: {MOOD_LABELS.get(mood, str(mood))} ({mood}/5)"
            )
            return

        if query.data.startswith(TAG_CALLBACK_PREFIX):
            _, action, tag = query.data.split(":", maxsplit=2)
            frontmatter = await self._repository.get_note_frontmatter(note_dt)
            current_tags = set(frontmatter.get("tags") or ["journal"])

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
        """Periodic task to send mood prompts after inactivity threshold."""
        now = datetime.now(UTC)
        active_chats = self._get_active_chats(context)
        for chat_id in list(active_chats):
            raw_chat_data = context.application.chat_data.get(chat_id)
            if not isinstance(raw_chat_data, dict):
                continue
            chat_data = raw_chat_data

            note_dt = effective_note_datetime(chat_data.get(OVERRIDE_DATE_KEY), now)
            note_has_entry = await self._repository.note_has_entry(note_dt)
            note_has_mood = await self._repository.note_has_mood(note_dt)

            last_entry_at = chat_data.get(LAST_ENTRY_AT_KEY)
            if not isinstance(last_entry_at, datetime):
                last_entry_at = await self._repository.get_last_entry_time(note_dt)

            should_prompt = should_prompt_for_mood(
                note_has_entry=note_has_entry,
                note_has_mood=note_has_mood,
                last_entry_at=last_entry_at,
                now=now,
                last_prompted_at=chat_data.get(LAST_PROMPT_AT_KEY),
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
        location_filter = filters.LOCATION & filters.ChatType.PRIVATE

        application.add_handler(MessageHandler(text_filter, self.handle_journal_entry))
        application.add_handler(MessageHandler(photo_filter, self.handle_journal_entry))
        application.add_handler(
            MessageHandler(location_filter, self.handle_journal_entry)
        )

        application.add_handler(CommandHandler("setdate", self.setdate_command))
        application.add_handler(CommandHandler("resetdate", self.resetdate_command))
        application.add_handler(CommandHandler("tags", self.tags_command))
        application.add_handler(CommandHandler("mood", self.mood_command))
        application.add_handler(CommandHandler("help", self.help_command))

        application.add_handler(CallbackQueryHandler(self.callback_router))
        application.add_error_handler(self.handle_error)

    def register_jobs(self, job_queue: JobQueue) -> None:  # type: ignore[type-arg]
        """Register periodic reminder jobs."""
        job_queue.run_repeating(self.check_mood_timers, interval=300, first=300)
