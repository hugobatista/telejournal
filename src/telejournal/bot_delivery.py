"""Delivery services for sending note text and attachments to Telegram chats."""

from __future__ import annotations

import logging
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from telegram.error import TelegramError

from telejournal.bot_helpers import _chunk_text, _history_render_keyboard
from telejournal.formatting import (
    AttachmentChunk,
    NoteRenderPayload,
    TextChunk,
    format_timestamp_as_prefixed_quote,
    parse_note_render_payload,
    strip_internal_tracking_markers,
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

    def resolve_attachment_path(
        self,
        attachment_rel: str,
        source_note_dt: datetime | None = None,
    ) -> Path | None:
        """Resolve a relative attachment path under the note directory or vault root."""
        repository = self._repository()
        vault_root = Path(repository.vault_root).resolve()

        candidate_roots: list[Path] = []
        if source_note_dt is not None:
            get_note_path = getattr(repository, "get_note_path", None)
            if callable(get_note_path):
                note_dir = get_note_path(source_note_dt).resolve().parent
                candidate_roots.append(note_dir)
            else:
                candidate_roots.append(
                    (vault_root / str(source_note_dt.year)).resolve()
                )
        candidate_roots.append(vault_root)

        for base_dir in candidate_roots:
            candidate = (base_dir / attachment_rel).resolve()

            if candidate != vault_root and vault_root not in candidate.parents:
                continue
            if candidate.exists() and candidate.is_file():
                return candidate

        return None

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
        repository = self._repository()
        attachment_path = self.resolve_attachment_path(
            attachment_rel,
            source_note_dt=source_note_dt,
        )
        if attachment_path is None:
            fetch_bytes = getattr(repository, "get_attachment_bytes", None)
            if not callable(fetch_bytes):
                await bot.send_message(
                    chat_id,
                    f"⚠️ Attachment not found: {attachment_rel}",
                )
                return

            payload = await fetch_bytes(attachment_rel)
            if payload is None:
                await bot.send_message(
                    chat_id,
                    f"⚠️ Attachment not found: {attachment_rel}",
                )
                return

            in_memory_attachment = BytesIO(payload)
            filename = Path(attachment_rel).name or "attachment"
            in_memory_attachment.name = filename
            await self._send_attachment_file(
                chat_id,
                bot,
                attachment_rel,
                in_memory_attachment,
                filename,
                source_note_dt,
            )
            return

        try:
            with attachment_path.open("rb") as opened_attachment:
                await self._send_attachment_file(
                    chat_id,
                    bot,
                    attachment_rel,
                    opened_attachment,
                    attachment_path.name,
                    source_note_dt,
                )
        except OSError:
            self._logger.exception("Failed to open attachment %s", attachment_rel)
            await bot.send_message(
                chat_id,
                f"⚠️ Failed to send attachment: {attachment_rel}",
            )

    async def _send_attachment_file(
        self,
        chat_id: int,
        bot: Any,
        attachment_rel: str,
        attachment_file: Any,
        attachment_name: str,
        source_note_dt: datetime | None,
    ) -> None:
        """Send one opened attachment object using extension-based media API."""
        suffix = Path(attachment_name).suffix.lower()

        try:
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
        sanitized_content = strip_internal_tracking_markers(note_content)
        formatted_content = format_timestamp_as_prefixed_quote(sanitized_content)
        await self.send_chunked_text(
            chat_id,
            bot,
            formatted_content,
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
