"""Tests for markdown and entry formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from telegram_journal_bot.formatting import (
    format_location_entry,
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
    """Text entries should include HH:MM prefix and stripped body."""
    dt = datetime(2026, 3, 7, 18, 34, tzinfo=UTC)
    assert format_text_entry(dt, "  hello  ") == "18:34 - hello"


def test_format_location_entry_quadrants() -> None:
    """Location format should include hemisphere and map URL."""
    dt = datetime(2026, 3, 7, 18, 35, tzinfo=UTC)
    rendered = format_location_entry(dt, -38.7223, 9.1393)
    assert "18:35 Location:" in rendered
    assert "38.7223° S, 9.1393° E" in rendered
    assert "https://maps.google.com/?q=-38.7223,9.1393" in rendered
