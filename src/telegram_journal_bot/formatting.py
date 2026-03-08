"""Formatting helpers for journal entry output."""

from __future__ import annotations

from datetime import datetime

from telegram import Message


def render_message_markdown(message: Message) -> str:
    """Render Telegram text or caption to markdown-compatible text."""
    if message.text:
        rendered = getattr(message, "text_markdown_urled", None)
        return rendered or message.text

    if message.caption:
        rendered = getattr(message, "caption_markdown_urled", None)
        return rendered or message.caption

    return ""


def format_text_entry(dt: datetime, text: str) -> str:
    """Format a text journal line with a timestamp."""
    return f"- {dt.strftime('%H:%M:%S')} > {text.strip()}"


def format_location_entry(dt: datetime, latitude: float, longitude: float) -> str:
    """Format a location entry line with map URL."""
    ns = "N" if latitude >= 0 else "S"
    ew = "E" if longitude >= 0 else "W"
    lat_abs = abs(latitude)
    lon_abs = abs(longitude)

    map_url = f"https://maps.google.com/?q={latitude},{longitude}"
    return (
        f"- {dt.strftime('%H:%M:%S')} > Location: "
        f"{lat_abs:.4f}° {ns}, {lon_abs:.4f}° {ew} "
        f"[Map]({map_url})"
    )
