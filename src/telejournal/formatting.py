"""Formatting helpers for journal entry output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from telegram import Message

MOOD_LABELS = {
    1: "😢",
    2: "😐",
    3: "😌",
    4: "🙂",
    5: "😊",
}

TG_ENTRY_START_TOKEN = "tg-entry-start"
TG_ENTRY_END_TOKEN = "tg-entry-end"

_ATTACHMENT_RE = re.compile(
    r"!\[\[(?P<target>[^\]]+)\]\]|!\[[^\]]*\]\((?P<md_target>[^)]+)\)"
)
_INTERNAL_TG_ENTRY_MARKER_RE = re.compile(
    rf"(?m)^<!-- (?:{TG_ENTRY_START_TOKEN}|{TG_ENTRY_END_TOKEN}):[^>]+ -->\n?"
)
_TIMESTAMP_MARKER_RE = re.compile(r"%%\s*(?P<time>\d{2}:\d{2}(?::\d{2})?)\s*%%")


@dataclass(frozen=True)
class TextChunk:
    """Represents a plain text section to send to Telegram."""

    text: str


@dataclass(frozen=True)
class AttachmentChunk:
    """Represents a file embed path extracted from note markdown."""

    attachment_rel: str


@dataclass(frozen=True)
class NoteRenderPayload:
    """Ordered collection of text and attachment chunks."""

    chunks: list[TextChunk | AttachmentChunk]


def parse_note_render_payload(note_content: str) -> NoteRenderPayload:
    """Parse note content into ordered text and attachment chunks.

    Obsidian-style embeds like ``![[2026/attachments/file.jpg]]`` are extracted as
    attachment chunks while preserving surrounding text in separate chunks.
    """
    sanitized_content = strip_internal_tracking_markers(note_content)
    formatted_content = format_timestamp_as_prefixed_quote(sanitized_content)
    chunks: list[TextChunk | AttachmentChunk] = []
    cursor = 0

    for match in _ATTACHMENT_RE.finditer(formatted_content):
        before = formatted_content[cursor : match.start()]
        if before:
            chunks.append(TextChunk(text=before))
        target = match.group("target")
        md_target = match.group("md_target")
        attachment_rel = None

        if target:
            attachment_rel = target.strip()
        elif md_target:
            md_target = md_target.strip()
            # Markdown image syntax can include a title after the URL
            # e.g. (path "title"). Take the first token as the path
            # and strip surrounding angle brackets or quotes.
            first = md_target.split(None, 1)[0]
            first = first.strip()
            if (first.startswith("<") and first.endswith(">")) or (
                first.startswith('"') and first.endswith('"')
            ):
                first = first[1:-1]
            attachment_rel = first

        if attachment_rel:
            # Keep first path component before Obsidian alias/heading fragments.
            attachment_rel = attachment_rel.split("|", maxsplit=1)[0]
            attachment_rel = attachment_rel.split("#", maxsplit=1)[0].strip()
        if attachment_rel:
            chunks.append(AttachmentChunk(attachment_rel=attachment_rel))

        cursor = match.end()

    tail = formatted_content[cursor:]
    if tail:
        chunks.append(TextChunk(text=tail))

    return NoteRenderPayload(chunks=chunks)


def strip_internal_tracking_markers(note_content: str) -> str:
    """Remove internal Telegram entry-tracking markers from note text."""
    return _INTERNAL_TG_ENTRY_MARKER_RE.sub("", note_content)


def build_message_marker(chat_id: int, message_id: int) -> str:
    """Build stable marker key for an entry generated from one message."""
    return f"{chat_id}:{message_id}"


def marker_start_comment(marker: str) -> str:
    """Return marker start line for persisted entry bodies."""
    return f"<!-- {TG_ENTRY_START_TOKEN}:{marker} -->"


def marker_end_comment(marker: str) -> str:
    """Return marker end line for persisted entry bodies."""
    return f"<!-- {TG_ENTRY_END_TOKEN}:{marker} -->"


def wrap_body_with_marker(body: str, marker: str) -> str:
    """Wrap body payload with marker comments for future in-place updates."""
    clean_body = body.strip()
    return f"{marker_start_comment(marker)}\n{clean_body}\n{marker_end_comment(marker)}"


def format_timestamp_as_prefixed_quote(content: str) -> str:
    """Replace timestamp markers with >-prefixed time format.

    Transforms ``%% HH:MM:SS %%`` to ``>HH:MM:SS`` for user display.
    """

    def _replace_with_quote_prefix(match: re.Match[str]) -> str:
        time_str = match.group("time")
        return f">{time_str}"

    return _TIMESTAMP_MARKER_RE.sub(_replace_with_quote_prefix, content)


def extract_mood_value(raw_mood: Any) -> int | None:
    """Extract current mood value from frontmatter."""
    if isinstance(raw_mood, int) and raw_mood in MOOD_LABELS:
        return raw_mood
    return None


def format_mood_change_text(previous_mood: int | None, current_mood: int) -> str:
    """Format note text describing a mood set/change event."""
    if previous_mood is not None and previous_mood in MOOD_LABELS:
        return (
            f"Mood changed {MOOD_LABELS[previous_mood]} ({previous_mood}/5)"
            f" -> {MOOD_LABELS[current_mood]} ({current_mood}/5)"
        )
    return f"Mood set to {MOOD_LABELS[current_mood]} ({current_mood}/5)"


def format_mood_saved_text(mood: int) -> str:
    """Format callback confirmation text after mood is persisted."""
    return f"Mood saved: {MOOD_LABELS[mood]} ({mood}/5)"


def format_timestamp_marker(dt: datetime) -> str:
    """Format a timestamp marker as a markdown comment line."""
    return f"%% {dt.strftime('%H:%M:%S')} %%"


def render_message_markdown(message: Message) -> str:
    """Render Telegram text or caption to markdown-compatible text."""
    if message.text:
        rendered = getattr(message, "text_markdown_urled", None)
        return rendered or message.text

    if message.caption:
        rendered = getattr(message, "caption_markdown_urled", None)
        return rendered or message.caption

    return ""


def extract_reply_quote(message: Message) -> str | None:
    """Extract quoted text from a self-reply or bot-reply, or None if not applicable."""
    reply_to = message.reply_to_message
    if not reply_to:
        return None

    if not message.from_user or not reply_to.from_user:
        return None

    is_self_reply = message.from_user.id == reply_to.from_user.id
    is_bot_reply = getattr(reply_to.from_user, "is_bot", False)
    if not is_self_reply and not is_bot_reply:
        return None

    # Extract text or caption from the replied message
    quoted_text = render_message_markdown(reply_to)
    if not quoted_text:
        # Handle media without caption
        if reply_to.photo:
            return "[Photo]"
        if reply_to.voice:
            return "[Voice message]"
        if reply_to.video:
            return "[Video]"
        if reply_to.video_note:
            return "[Video note]"
        if reply_to.location:
            return "[Location]"
        return None

    return quoted_text


def format_with_quote(quote: str, content: str) -> str:
    """Format content with a markdown quote block prefix."""
    quote_lines = [f"> {line}" if line else ">" for line in quote.split("\n")]
    quote_block = "\n".join(quote_lines)
    return f"{quote_block}\n\n{content}"


def format_text_entry(text: str) -> str:
    """Format a plain text journal line without timestamp decoration."""
    return text.strip()


def format_location_entry(latitude: float, longitude: float) -> str:
    """Format a location entry line with map URL."""
    ns = "N" if latitude >= 0 else "S"
    ew = "E" if longitude >= 0 else "W"
    lat_abs = abs(latitude)
    lon_abs = abs(longitude)

    map_url = f"https://maps.google.com/?q={latitude},{longitude}"
    return f"Location: {lat_abs:.4f}° {ns}, {lon_abs:.4f}° {ew} [Map]({map_url})"


def format_photo_entry(caption: str, attachment_rel: str, fallback: str) -> str:
    """Format a single photo entry with optional caption and embed."""
    heading = format_text_entry(caption) if caption else fallback
    return f"{heading}\n![[{attachment_rel}]]"


def format_album_entry(caption: str, images: list[str], fallback: str) -> str:
    """Format a photo album entry as heading followed by embeds."""
    heading = format_text_entry(caption) if caption else fallback
    return "\n".join([heading, *images])


def format_entry_block(dt: datetime, body: str, include_timestamp: bool) -> str:
    """Render a persisted entry with optional timestamp marker line."""
    clean_body = body.strip()
    if not include_timestamp:
        return clean_body

    marker = format_timestamp_marker(dt)
    if not clean_body:
        return marker
    return f"{marker}\n{clean_body}"
