"""Delivery services for sending note text and attachments to Telegram chats."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from telegram.error import TelegramError

from telejournal.bot_helpers import _chunk_text, _history_render_keyboard
from telejournal.formatting import (
    AttachmentChunk,
    NoteRenderPayload,
    TextChunk,
    parse_note_render_payload,
)

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
VOICE_EXTENSIONS = {".ogg", ".opus"}


class NoteDeliveryService:
    """Send rendered notes, raw notes, and attachments in Telegram-safe chunks."""

    def __init__(
        self,
        repository_provider: Callable[[], Any],
        track_reply_source_message: Callable[[int, Any, datetime], None],
        no_memories_message: str,
        logger: logging.Logger,
    ) -> None:
        """Initialize service with deferred repository access and send callbacks."""
        self._repository_provider = repository_provider
        self._track_reply_source_message = track_reply_source_message
        self._no_memories_message = no_memories_message
        self._logger = logger

    def _repository(self) -> Any:
        """Return the current repository instance from provider."""
        return self._repository_provider()

    def resolve_attachment_path(self, attachment_rel: str) -> Path | None:
        """Resolve a relative attachment path under vault root safely."""
        repository = self._repository()
        vault_root = Path(repository.vault_root).resolve()
        candidate = (vault_root / attachment_rel).resolve()

        if candidate != vault_root and vault_root not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    async def send_chunked_text(
        self,
        chat_id: int,
        bot: Any,
        text: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send text using Telegram-safe chunk sizes."""
        if not text or not text.strip():
            return
        for payload in _chunk_text(text):
            sent = await bot.send_message(chat_id, payload)
            if source_note_dt is not None:
                self._track_reply_source_message(chat_id, sent, source_note_dt)

    async def send_attachment(
        self,
        chat_id: int,
        bot: Any,
        attachment_rel: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send one attachment based on file extension with graceful fallback."""
        attachment_path = self.resolve_attachment_path(attachment_rel)
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
                    sent = await bot.send_photo(chat_id, attachment_file)
                elif suffix in VIDEO_EXTENSIONS:
                    sent = await bot.send_video(chat_id, attachment_file)
                elif suffix in VOICE_EXTENSIONS:
                    sent = await bot.send_voice(chat_id, attachment_file)
                else:
                    sent = await bot.send_document(chat_id, attachment_file)

                if source_note_dt is not None:
                    self._track_reply_source_message(chat_id, sent, source_note_dt)
        except (OSError, TelegramError):
            self._logger.exception("Failed to send attachment %s", attachment_rel)
            await bot.send_message(
                chat_id,
                f"⚠️ Failed to send attachment: {attachment_rel}",
            )

    async def send_note_payload(
        self,
        chat_id: int,
        bot: Any,
        payload: NoteRenderPayload,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send parsed note chunks as text and media in source order."""
        for chunk in payload.chunks:
            if isinstance(chunk, TextChunk):
                await self.send_chunked_text(
                    chat_id,
                    bot,
                    chunk.text,
                    source_note_dt=source_note_dt,
                )
            elif isinstance(chunk, AttachmentChunk):
                await self.send_attachment(
                    chat_id,
                    bot,
                    chunk.attachment_rel,
                    source_note_dt=source_note_dt,
                )

    async def send_note_content(
        self,
        chat_id: int,
        bot: Any,
        note_content: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Parse note content and send it to a Telegram chat."""
        payload = parse_note_render_payload(note_content)
        await self.send_note_payload(
            chat_id,
            bot,
            payload,
            source_note_dt=source_note_dt,
        )

    async def send_note_text_only(
        self,
        chat_id: int,
        bot: Any,
        note_content: str,
        source_note_dt: datetime | None = None,
    ) -> None:
        """Send note content exactly as text, preserving embed links."""
        await self.send_chunked_text(
            chat_id,
            bot,
            note_content,
            source_note_dt=source_note_dt,
        )

    async def send_historical_notes_for_chat(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
        render_mode: str,
    ) -> None:
        """Send historical notes in selected mode for one chat."""
        historical_notes = await self._repository().get_same_day_previous_year_notes(
            reference_dt
        )

        if not historical_notes:
            await bot.send_message(chat_id, self._no_memories_message)
            return

        date_label = reference_dt.strftime("%m-%d")
        await bot.send_message(chat_id, f"📅 On this day ({date_label})")
        for note_dt, content in historical_notes:
            sent = await bot.send_message(
                chat_id,
                f"==== {note_dt.strftime('%Y-%m-%d')} ====",
            )
            self._track_reply_source_message(chat_id, sent, note_dt)
            if render_mode == "raw":
                await self.send_note_text_only(
                    chat_id,
                    bot,
                    content,
                    source_note_dt=note_dt,
                )
            else:
                await self.send_note_content(
                    chat_id,
                    bot,
                    content,
                    source_note_dt=note_dt,
                )

    async def send_history_brief_prompt(
        self,
        chat_id: int,
        bot: Any,
        reference_dt: datetime,
    ) -> None:
        """Send brief summary of available years and ask for output format."""
        historical_notes = await self._repository().get_same_day_previous_year_notes(
            reference_dt
        )

        if not historical_notes:
            await bot.send_message(chat_id, self._no_memories_message)
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
