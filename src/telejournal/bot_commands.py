"""Command handler service for bot command entry points."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from telejournal.bot_helpers import (
    _delete_confirmation_keyboard,
    _history_render_keyboard,
    _mood_keyboard,
    _parse_iso_date,
    _parse_tags_from_args,
    _tags_keyboard,
    _truncate_message,
)
from telejournal.command_registry import visible_help_lines
from telejournal.logic import effective_note_datetime, parse_setdate_args


class CommandHandlerService:
    """Own command-handler behavior, keeping bot wiring lightweight."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any],
        settings_provider: Callable[[], Any],
        is_private_and_authorized: Callable[[Update], bool],
        chat_data_resolver: Callable[[ContextTypes.DEFAULT_TYPE], dict[str, Any]],
        config_summary: Callable[[], str],
        config_keyboard: Callable[[], Any],
        safe_user_error: Callable[[Update, str], Any],
        override_date_key: str,
        logger: logging.Logger,
    ) -> None:
        """Store command dependencies and shared state keys."""
        self._repository_provider = repository_provider
        self._settings_provider = settings_provider
        self._is_private_and_authorized = is_private_and_authorized
        self._chat_data_resolver = chat_data_resolver
        self._config_summary = config_summary
        self._config_keyboard = config_keyboard
        self._safe_user_error = safe_user_error
        self._override_date_key = override_date_key
        self._logger = logger

    def _repository(self) -> Any:
        """Return active repository instance."""
        return self._repository_provider()

    def _settings(self) -> Any:
        """Return current settings instance."""
        return self._settings_provider()

    async def help_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send command usage information."""
        del context
        if not self._is_private_and_authorized(update):
            return

        command_lines = visible_help_lines(self._settings().storage_provider)

        help_text = (
            "📝 Telejournal Bot Usage\n\n"
            "• Every private message is journaled\n"
            "• Photos are embedded from attachments/\n"
            "• Voice recordings are embedded from attachments/\n"
            "• Video messages (including circular video notes) are embedded from attachments/\n"
            "• Messages within the configured time window share one timestamp\n"
            "• Mood tracked via /mood (😢 😐 😌 🙂 😊)\n\n"
            + "Commands:\n"
            + "\n".join(command_lines)
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
            self._logger.warning("Invalid /setdate input: %s", args)
            await self._safe_user_error(
                update,
                "❌ Use: /setdate YYYY-MM-DD [HH:MM:SS]",
            )
            return

        chat_data = self._chat_data_resolver(context)
        chat_data[self._override_date_key] = override_dt
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

        chat_data = self._chat_data_resolver(context)
        chat_data.pop(self._override_date_key, None)
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

        chat_data = self._chat_data_resolver(context)
        default_note_dt = effective_note_datetime(
            chat_data.get(self._override_date_key),
            datetime.now(UTC),
        )

        if not update.effective_message:
            return

        args = context.args or []
        if not args:
            preview = await self._repository().peek_last_entry(default_note_dt)
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

        note_content = await self._repository().get_note_content(note_dt)
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

        chat_data = self._chat_data_resolver(context)
        note_dt = effective_note_datetime(
            chat_data.get(self._override_date_key),
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

        note_content = await self._repository().get_note_content(note_dt)
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

        chat_data = self._chat_data_resolver(context)
        note_dt = effective_note_datetime(
            chat_data.get(self._override_date_key),
            datetime.now(UTC),
        )
        frontmatter = await self._repository().get_note_frontmatter(note_dt)
        current_tags = set(frontmatter.get("tags") or ["journal"])

        args = context.args or []
        if args:
            parsed_tags = _parse_tags_from_args(args)
            if parsed_tags:
                current_tags.update(parsed_tags)
                await self._repository().update_frontmatter(
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
                reply_markup=_tags_keyboard(current_tags, self._settings().tag_choices),
            )

    async def settings_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Start guided runtime configuration for supported settings."""
        del context
        if not self._is_private_and_authorized(update):
            return
        if not update.effective_message:
            return

        await update.effective_message.reply_text(
            f"{self._config_summary()}\n\nChoose one setting to update:",
            reply_markup=self._config_keyboard(),
        )

    async def storageauth_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Run storage device authorization steps for first-time setup."""
        if not self._is_private_and_authorized(update):
            return
        if not update.effective_message:
            return

        repository = self._repository()
        start_auth = getattr(repository, "start_device_authorization", None)
        complete_auth = getattr(repository, "complete_device_authorization", None)
        show_instructions = getattr(
            repository, "build_authorization_instructions", None
        )
        is_authorized = getattr(repository, "is_authorized", None)

        if not callable(show_instructions):
            await update.effective_message.reply_text(
                "Authorization workflow is unavailable for this storage backend."
            )
            return

        action = "auto"
        if context.args:
            action = context.args[0].strip().lower()

        try:
            if action in {"start", "restart"}:
                if not callable(start_auth):
                    raise RuntimeError("OneDrive auth start is unavailable")
                result = start_auth()
                await update.effective_message.reply_text(result)
                return

            if action in {"complete", "poll"}:
                if not callable(complete_auth):
                    raise RuntimeError("OneDrive auth completion is unavailable")
                result = complete_auth()
                await update.effective_message.reply_text(result)
                return

            if action in {"auto", ""}:
                if callable(is_authorized) and bool(is_authorized()):
                    await update.effective_message.reply_text(
                        "Storage backend is already authorized."
                    )
                    return

                if callable(complete_auth):
                    result = complete_auth()
                    await update.effective_message.reply_text(result)
                    return

                action = "status"

            if action != "status":
                await update.effective_message.reply_text(
                    "Use /storageauth [start|complete|status]"
                )
                return

            if callable(is_authorized) and bool(is_authorized()):
                await update.effective_message.reply_text(
                    "Storage backend is already authorized."
                )
                return

            instructions = show_instructions()
            if instructions is None:
                await update.effective_message.reply_text(
                    "Storage backend is already authorized."
                )
                return
            await update.effective_message.reply_text(instructions)
        except RuntimeError as exc:
            await update.effective_message.reply_text(str(exc))
