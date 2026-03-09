"""Formatting helpers for journal entry output."""

from __future__ import annotations

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
    """Extract quoted text from a self-reply message, or None if not applicable."""
    reply_to = message.reply_to_message
    if not reply_to:
        return None

    # Only quote self-replies (same user)
    if not message.from_user or not reply_to.from_user:
        return None
    if message.from_user.id != reply_to.from_user.id:
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
    return (
        "Location: "
        f"{lat_abs:.4f}° {ns}, {lon_abs:.4f}° {ew} "
        f"[Map]({map_url})"
    )


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
