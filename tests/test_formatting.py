"""Tests for markdown and entry formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from telegram_journal_bot.formatting import (
    MOOD_LABELS,
    format_mood_change_text,
    format_mood_saved_text,
    format_album_entry,
    format_entry_block,
    format_location_entry,
    format_photo_entry,
    format_timestamp_marker,
    format_text_entry,
    render_message_markdown,
)


def test_render_message_markdown_prefers_text_markdown() -> None:
    """Markdown-rendered text should take precedence over raw text."""
    message = SimpleNamespace(text="raw", text_markdown_urled="**raw**", caption=None)
    assert render_message_markdown(message) == "**raw**"  # type: ignore[arg-type]


def test_render_message_markdown_uses_caption() -> None:
    """Caption fallback should work when no text exists."""
    message = SimpleNamespace(
        text=None,
        caption="cap",
        caption_markdown_urled="*cap*",
    )
    assert render_message_markdown(message) == "*cap*"  # type: ignore[arg-type]


def test_render_message_markdown_empty() -> None:
    """Absent text and caption should render as empty string."""
    message = SimpleNamespace(text=None, caption=None)
    assert render_message_markdown(message) == ""  # type: ignore[arg-type]


def test_format_text_entry() -> None:
    """Text entries should be stripped plain text without bullets."""
    assert format_text_entry("  hello  ") == "hello"


def test_format_timestamp_marker() -> None:
    """Timestamp markers should use markdown comment format."""
    dt = datetime(2026, 3, 7, 18, 34, tzinfo=UTC)
    assert format_timestamp_marker(dt) == "%% 18:34:00 %%"


def test_format_location_entry_quadrants() -> None:
    """Location format should include hemisphere and map URL."""
    rendered = format_location_entry(-38.7223, 9.1393)
    assert "Location:" in rendered
    assert "38.7223° S, 9.1393° E" in rendered
    assert "https://maps.google.com/?q=-38.7223,9.1393" in rendered


def test_format_photo_and_album_entries() -> None:
    """Photo formatters should build heading plus embeds."""
    assert format_photo_entry("cap", "2026/attachments/a.jpg", "Photo") == (
        "cap\n![[2026/attachments/a.jpg]]"
    )
    assert format_photo_entry("", "2026/attachments/a.jpg", "Photo") == (
        "Photo\n![[2026/attachments/a.jpg]]"
    )
    assert format_album_entry("album", ["![[a.jpg]]", "![[b.jpg]]"], "Photo album") == (
        "album\n![[a.jpg]]\n![[b.jpg]]"
    )


def test_format_entry_block() -> None:
    """Entry block should prepend marker line only when requested."""
    dt = datetime(2026, 3, 7, 18, 34, tzinfo=UTC)
    assert format_entry_block(dt, "hello", include_timestamp=True) == (
        "%% 18:34:00 %%\nhello"
    )
    assert format_entry_block(dt, " hello ", include_timestamp=False) == "hello"
    assert format_entry_block(dt, "   ", include_timestamp=True) == "%% 18:34:00 %%"


def test_format_mood_messages() -> None:
    """Mood text should render consistently for set/change/saved states."""
    assert format_mood_change_text(None, 4) == f"Mood set to {MOOD_LABELS[4]} (4/5)"
    assert format_mood_change_text(2, 5) == (
        f"Mood changed {MOOD_LABELS[2]} (2/5) -> {MOOD_LABELS[5]} (5/5)"
    )
    assert format_mood_saved_text(3) == f"Mood saved: {MOOD_LABELS[3]} (3/5)"
