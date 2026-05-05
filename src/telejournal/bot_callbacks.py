"""Callback routing service for Telegram inline-button interactions."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from telegram import Update
from telegram.ext import ContextTypes

from telejournal.bot_helpers import (
    DELETE_CALLBACK_PREFIX,
    HISTORY_CALLBACK_PREFIX,
    MOOD_CALLBACK_PREFIX,
    TAG_CALLBACK_PREFIX,
    _parse_iso_date,
    _tags_keyboard,
    _truncate_message,
)
from telejournal.formatting import (
    MOOD_LABELS,
    extract_mood_value,
    format_entry_block,
    format_mood_change_text,
    format_mood_saved_text,
    format_text_entry,
)


class CallbackRouterService:
    """Handle callback-query routes using injected bot dependencies."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any],
        settings_provider: Callable[[], Any],
        apply_runtime_config: Callable[[str, Any, ContextTypes.DEFAULT_TYPE], str],
        config_summary: Callable[[], str],
        config_keyboard: Callable[[], Any],
        config_prompt_bool_keyboard: Callable[[str], Any],
        config_confirm_keyboard: Callable[[], Any],
        chat_id_resolver: Callable[[Update], int | None],
        send_note_text_only: Callable[..., Any],
        send_note_content: Callable[..., Any],
        send_historical_notes_for_chat: Callable[..., Any],
        should_include_timestamp: Callable[[dict[str, Any], datetime, datetime], bool],
        record_entry: Callable[..., Any],
        config_callback_prefix: str,
        config_flow_key: str,
        config_pending_key: str,
        last_prompt_at_key: str,
        last_prompt_note_key: str,
        logger: logging.Logger,
    ) -> None:
        """Store routing dependencies and runtime state keys."""
        self._repository_provider = repository_provider
        self._settings_provider = settings_provider
        self._apply_runtime_config = apply_runtime_config
        self._config_summary = config_summary
        self._config_keyboard = config_keyboard
        self._config_prompt_bool_keyboard = config_prompt_bool_keyboard
        self._config_confirm_keyboard = config_confirm_keyboard
        self._chat_id_resolver = chat_id_resolver
        self._send_note_text_only = send_note_text_only
        self._send_note_content = send_note_content
        self._send_historical_notes_for_chat = send_historical_notes_for_chat
        self._should_include_timestamp = should_include_timestamp
        self._record_entry = record_entry
        self._config_callback_prefix = config_callback_prefix
        self._config_flow_key = config_flow_key
        self._config_pending_key = config_pending_key
        self._last_prompt_at_key = last_prompt_at_key
        self._last_prompt_note_key = last_prompt_note_key
        self._logger = logger

    def _repository(self) -> Any:
        """Return the active repository instance."""
        return self._repository_provider()

    def _settings(self) -> Any:
        """Return current settings snapshot."""
        return self._settings_provider()

    async def route(
        self,
        *,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        chat_data: dict[str, Any],
        note_dt: datetime,
        now: datetime,
    ) -> None:
        """Route callback-query payloads to config/history/mood/delete/tag handlers."""
        query = update.callback_query
        if query is None or not query.data:  # pragma: no cover
            return

        if query.data.startswith(self._config_callback_prefix):
            await self._handle_config(query, context, chat_data)
            return

        if query.data.startswith(HISTORY_CALLBACK_PREFIX):
            await self._handle_history(query, update, context)
            return

        if query.data.startswith(MOOD_CALLBACK_PREFIX):
            await self._handle_mood(query, chat_data, note_dt, now)
            return

        if query.data.startswith(DELETE_CALLBACK_PREFIX):
            await self._handle_delete(query)
            return

        if query.data.startswith(TAG_CALLBACK_PREFIX):
            await self._handle_tags(query, chat_data, note_dt)

    async def _handle_config(
        self,
        query: Any,
        context: ContextTypes.DEFAULT_TYPE,
        chat_data: dict[str, Any],
    ) -> None:
        """Handle guided /settings callback interactions."""
        parts = query.data.split(":")
        if len(parts) < 2:  # pragma: no cover
            return
        action = parts[1]

        if action == "edit" and len(parts) == 3:
            key = parts[2]
            if key == "tag_choices":
                chat_data[self._config_flow_key] = {
                    "state": "await_input",
                    "field": key,
                }
                chat_data.pop(self._config_pending_key, None)
                await query.edit_message_text(
                    "Send the new tag list as CSV.\n"
                    "Rules: lowercase, numbers, '_' or '-'.\n"
                    "Example: family,health,love"
                )
                return

            if key == "daily_brief_time_utc":
                chat_data[self._config_flow_key] = {
                    "state": "await_input",
                    "field": key,
                }
                chat_data.pop(self._config_pending_key, None)
                await query.edit_message_text(
                    "Send UTC time as 0, HH:MM, or HH:MM:SS.\n"
                    "Examples: 0, 09:00, 09:00:30"
                )
                return

            if key in {"prompt_for_mood_if_missing", "bot_menu_enabled"}:
                chat_data[self._config_flow_key] = {"state": "await_prompt_button"}
                chat_data.pop(self._config_pending_key, None)
                await query.edit_message_text(
                    "Choose the new boolean value:",
                    reply_markup=self._config_prompt_bool_keyboard(key),
                )
                return
            return

        if action == "set_bool" and len(parts) == 4:
            pending_key = parts[2].strip()
            if pending_key not in {"prompt_for_mood_if_missing", "bot_menu_enabled"}:
                return

            raw_value = parts[3].strip().lower()
            if raw_value not in {"true", "false"}:
                return
            value = raw_value == "true"
            chat_data[self._config_pending_key] = {
                "key": pending_key,
                "value": value,
            }
            chat_data[self._config_flow_key] = {"state": "await_confirm"}
            await query.edit_message_text(
                f"Apply {pending_key} = " f"{'true' if value else 'false'}?",
                reply_markup=self._config_confirm_keyboard(),
            )
            return

        if action == "confirm":
            pending = chat_data.get(self._config_pending_key)
            if not isinstance(pending, dict):
                await query.edit_message_text(
                    "No pending config update. Use /settings.",
                )
                return

            pending_key = pending.get("key")
            pending_raw_value = pending.get("value")
            if not isinstance(pending_key, str):
                await query.edit_message_text(
                    "Invalid pending config state. Use /settings.",
                )
                return

            from telejournal.config import (
                _parse_daily_brief_time_utc,
                _parse_tag_choices,
            )

            try:
                parsed_value: Any = pending_raw_value
                if pending_key == "tag_choices":
                    parsed_value = _parse_tag_choices(pending_raw_value)
                elif pending_key == "daily_brief_time_utc":
                    parsed_value = _parse_daily_brief_time_utc(pending_raw_value)
                elif pending_key in {
                    "prompt_for_mood_if_missing",
                    "bot_menu_enabled",
                }:
                    parsed_value = bool(pending_raw_value)

                confirmation = self._apply_runtime_config(
                    pending_key,
                    parsed_value,
                    context,
                )
            except ValueError:
                await query.edit_message_text(
                    "Validation failed. Please restart with /settings.",
                )
                return
            except OSError:
                await query.edit_message_text(
                    "Failed to persist config to disk. "
                    "Check file permissions and try again."
                )
                return

            chat_data.pop(self._config_pending_key, None)
            chat_data.pop(self._config_flow_key, None)
            await query.edit_message_text(
                f"✅ {confirmation}\n\n{self._config_summary()}"
            )
            return

        if action == "cancel":
            chat_data.pop(self._config_pending_key, None)
            chat_data.pop(self._config_flow_key, None)
            await query.edit_message_text("Configuration update canceled.")
            return

        if action == "back":
            chat_data.pop(self._config_pending_key, None)
            chat_data.pop(self._config_flow_key, None)
            await query.edit_message_text(
                f"{self._config_summary()}\n\nChoose one setting to update:",
                reply_markup=self._config_keyboard(),
            )

    async def _handle_history(
        self,
        query: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle show/history callbacks with raw or rendered output modes."""
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

        chat_id = self._chat_id_resolver(update)
        if chat_id is None:
            return

        if action == "show":
            note_content = await self._repository().get_note_content(target_dt)
            if note_content is None:
                await query.edit_message_text(
                    f"No note found for {target_dt.strftime('%Y-%m-%d')}."
                )
                return

            await query.edit_message_text("Sending note...")
            if render_mode == "raw":
                await self._send_note_text_only(
                    chat_id,
                    context.bot,
                    note_content,
                    source_note_dt=target_dt,
                )
            else:
                await self._send_note_content(
                    chat_id,
                    context.bot,
                    note_content,
                    source_note_dt=target_dt,
                )
            return

        await query.edit_message_text("Sending memories...")
        await self._send_historical_notes_for_chat(
            chat_id,
            context.bot,
            target_dt,
            render_mode,
        )

    async def _handle_mood(
        self,
        query: Any,
        chat_data: dict[str, Any],
        note_dt: datetime,
        now: datetime,
    ) -> None:
        """Handle mood save callbacks and append mood change log entries."""
        raw_value = query.data.removeprefix(MOOD_CALLBACK_PREFIX)
        try:
            mood = int(raw_value)
        except ValueError:
            return

        if mood not in MOOD_LABELS:
            return

        frontmatter = await self._repository().get_note_frontmatter(note_dt)
        previous = extract_mood_value(frontmatter.get("mood"))

        await self._repository().update_frontmatter(note_dt, {"mood": mood})

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

        chat_data[self._last_prompt_at_key] = now
        chat_data[self._last_prompt_note_key] = note_dt.strftime("%Y-%m-%d")
        await query.edit_message_text(format_mood_saved_text(mood))

    async def _handle_delete(self, query: Any) -> None:
        """Handle delete confirmation callbacks for entry/day operations."""
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
            deleted = await self._repository().delete_last_entry(target_note_dt)
            if deleted is None:
                await query.edit_message_text("No entries to delete.")
                return
            deleted_preview = _truncate_message(deleted, max_len=3600)
            await query.edit_message_text(
                f"🗑️ Deleted last entry:\n\n{deleted_preview}"
            )
            return

        if action == "day":
            deleted_day = await self._repository().delete_day(target_note_dt)
            if not deleted_day:
                await query.edit_message_text(
                    f"No note found for {target_note_dt.strftime('%Y-%m-%d')}."
                )
                return
            await query.edit_message_text(
                f"🗑️ Deleted day {target_note_dt.strftime('%Y-%m-%d')}."
            )

    async def _handle_tags(
        self,
        query: Any,
        chat_data: dict[str, Any],
        note_dt: datetime,
    ) -> None:
        """Handle add/remove tag callbacks with strict validation."""
        del chat_data
        try:
            _, action, tag = query.data.split(":", maxsplit=2)
        except ValueError:
            self._logger.warning("Invalid tag callback data format: %s", query.data)
            return

        if action not in ("add", "remove"):
            self._logger.warning("Invalid tag action: %s", action)
            return

        frontmatter = await self._repository().get_note_frontmatter(note_dt)
        current_tags = set(frontmatter.get("tags") or ["journal"])

        if tag not in self._settings().tag_choices and tag not in current_tags:
            self._logger.warning(
                "Invalid tag value (not in choices or existing): %s",
                tag,
            )
            return

        if action == "add":
            current_tags.add(tag)
        elif tag != "journal":
            current_tags.discard(tag)

        updates = {"tags": sorted(current_tags)}
        await self._repository().update_frontmatter(note_dt, updates)

        rendered_tags = ", ".join(sorted(current_tags))
        await query.edit_message_text(
            f"Current: {rendered_tags}",
            reply_markup=_tags_keyboard(
                current_tags,
                self._settings().tag_choices,
            ),
        )
