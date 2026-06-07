"""Media entry persistence service for Telegram journal messages."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from telegram import Update
from telegram.ext import ContextTypes, Job

from telejournal.formatting import (
    format_album_entry,
    format_entry_block,
    format_location_entry,
    format_photo_entry,
    format_text_entry,
    format_with_quote,
    render_message_markdown,
)


class MediaEntryService:
    """Persist media and text messages into journal note entries."""

    def __init__(
        self,
        repository_provider: Callable[[], Any],
        chat_data_resolver: Callable[[ContextTypes.DEFAULT_TYPE], dict[str, Any]],
        extract_reply_quote_with_source_link: Callable[
            [Any, int, datetime], str | None
        ],
        record_message_entry: Callable[..., Awaitable[None]],
        record_entry: Callable[..., Awaitable[None]],
        albums_key: str,
        album_job_prefix: str,
        album_flush_seconds: int,
        logger: logging.Logger,
    ) -> None:
        """Initialize callbacks and constants used by media entry handlers."""
        self._repository_provider = repository_provider
        self._chat_data_resolver = chat_data_resolver
        self._extract_reply_quote_with_source_link = (
            extract_reply_quote_with_source_link
        )
        self._record_message_entry = record_message_entry
        self._record_entry = record_entry
        self._albums_key = albums_key
        self._album_job_prefix = album_job_prefix
        self._album_flush_seconds = album_flush_seconds
        self._logger = logger

    def _repository(self) -> Any:
        """Return the current repository instance from provider."""
        return self._repository_provider()

    async def handle_photo(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
        flush_album_callback: Callable[[ContextTypes.DEFAULT_TYPE], Awaitable[None]],
    ) -> bool:
        """Persist a photo and append embed entry in note."""
        message = update.effective_message
        if not message or not message.photo:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        best_photo = message.photo[-1]
        attachment_rel = await self._repository().save_photo(best_photo, note_dt, ts)
        caption = render_message_markdown(message)

        media_group_id = message.media_group_id
        if media_group_id and context.job_queue is not None:
            chat_data = self._chat_data_resolver(context)
            albums = chat_data.setdefault(self._albums_key, {})

            quote = None
            if media_group_id not in albums:
                quote = self._extract_reply_quote_with_source_link(
                    message,
                    chat_id,
                    note_dt,
                )

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

            job_name = f"{self._album_job_prefix}:{chat_id}:{media_group_id}"
            if context.job_queue is not None:
                if not context.job_queue.get_jobs_by_name(job_name):
                    context.job_queue.run_once(
                        cast(Any, flush_album_callback),
                        when=self._album_flush_seconds,
                        data={"chat_id": chat_id, "media_group_id": media_group_id},
                        name=job_name,
                    )
            return False

        heading = format_photo_entry(caption, attachment_rel, "Photo")

        quote = self._extract_reply_quote_with_source_link(
            message,
            chat_id,
            note_dt,
        )
        if quote:
            heading = format_with_quote(quote, heading)

        chat_data = self._chat_data_resolver(context)
        await self._record_message_entry(
            chat_data,
            note_dt,
            heading,
            include_timestamp,
            message_marker,
            as_continuation=not include_timestamp,
        )
        return True

    async def flush_album_entry(
        self,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int | None:
        """Flush a buffered album to a single note entry."""
        job: Job[Any] | None = context.job
        if job is None or not isinstance(job.data, dict):
            return None

        chat_id = job.data.get("chat_id")
        media_group_id = job.data.get("media_group_id")
        if chat_id is None or media_group_id is None:
            return None

        chat_data = context.application.chat_data.get(chat_id)
        if not isinstance(chat_data, dict):
            return None

        albums = chat_data.get(self._albums_key)
        if not isinstance(albums, dict):
            return None

        album_state = albums.pop(media_group_id, None)
        if not isinstance(album_state, dict):
            return None

        note_dt = album_state.get("note_dt")
        images = album_state.get("images") or []
        caption = album_state.get("caption") or ""
        quote = album_state.get("quote")
        if not isinstance(note_dt, datetime) or not images:
            return None

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
            return int(chat_id)
        except OSError:
            self._logger.exception(
                "Vault write failed while flushing album for chat_id=%s",
                chat_id,
            )
        return None

    async def handle_location(
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
        body = format_location_entry(latitude, longitude)

        message = update.effective_message
        if message:
            quote = self._extract_reply_quote_with_source_link(
                message,
                chat_id,
                note_dt,
            )
            if quote:
                body = format_with_quote(quote, body)

        location_data = {
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
        }
        chat_data = self._chat_data_resolver(context)
        await self._record_message_entry(
            chat_data,
            note_dt,
            body,
            include_timestamp,
            message_marker,
            frontmatter_updates={"location": location_data},
            as_continuation=not include_timestamp,
        )

    async def handle_text(
        self,
        message_text: str,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        message_marker: str,
        quote: str | None = None,
    ) -> None:
        """Persist a text message as a journal line."""
        body = format_text_entry(message_text)
        if quote:
            body = format_with_quote(quote, body)
        chat_data = self._chat_data_resolver(context)
        await self._record_message_entry(
            chat_data,
            note_dt,
            body,
            include_timestamp,
            message_marker,
            as_continuation=not include_timestamp,
        )

    async def handle_voice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a voice recording and append embed entry in note."""
        message = update.effective_message
        if not message or not message.voice:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        attachment_rel = await self._repository().save_voice(message.voice, note_dt, ts)
        caption = render_message_markdown(message)
        body = format_photo_entry(caption, attachment_rel, "Voice recording")

        quote = self._extract_reply_quote_with_source_link(
            message,
            chat_id,
            note_dt,
        )
        if quote:
            body = format_with_quote(quote, body)

        chat_data = self._chat_data_resolver(context)
        await self._record_message_entry(
            chat_data,
            note_dt,
            body,
            include_timestamp,
            message_marker,
            as_continuation=not include_timestamp,
        )
        return True

    async def handle_video(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a video message and append embed entry in note."""
        message = update.effective_message
        if not message or not message.video:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        attachment_rel = await self._repository().save_video(message.video, note_dt, ts)
        caption = render_message_markdown(message)
        body = format_photo_entry(caption, attachment_rel, "Video message")

        quote = self._extract_reply_quote_with_source_link(
            message,
            chat_id,
            note_dt,
        )
        if quote:
            body = format_with_quote(quote, body)

        chat_data = self._chat_data_resolver(context)
        await self._record_message_entry(
            chat_data,
            note_dt,
            body,
            include_timestamp,
            message_marker,
            as_continuation=not include_timestamp,
        )
        return True

    async def handle_video_note(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        note_dt: datetime,
        include_timestamp: bool,
        chat_id: int,
        message_marker: str,
    ) -> bool:
        """Persist a video note (circular video) and append embed entry in note."""
        message = update.effective_message
        if not message or not message.video_note:
            return False

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        attachment_rel = await self._repository().save_video_note(
            message.video_note,
            note_dt,
            ts,
        )
        caption = render_message_markdown(message)
        body = format_photo_entry(caption, attachment_rel, "Video note")

        quote = self._extract_reply_quote_with_source_link(
            message,
            chat_id,
            note_dt,
        )
        if quote:
            body = format_with_quote(quote, body)

        chat_data = self._chat_data_resolver(context)
        await self._record_message_entry(
            chat_data,
            note_dt,
            body,
            include_timestamp,
            message_marker,
            as_continuation=not include_timestamp,
        )
        return True
