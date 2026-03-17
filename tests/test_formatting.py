"""Tests for markdown and entry formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from telejournal.formatting import (
    AttachmentChunk,
    MOOD_LABELS,
    NoteRenderPayload,
    TextChunk,
    extract_reply_quote,
    format_mood_change_text,
    format_mood_saved_text,
    format_with_quote,
    format_album_entry,
    format_entry_block,
    format_location_entry,
    format_photo_entry,
    format_timestamp_marker,
    format_text_entry,
    render_message_markdown,
    parse_note_render_payload,
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


def test_extract_reply_quote_self_reply() -> None:
    """Self-reply should extract quoted text from original message."""
    replied_msg = SimpleNamespace(
        text="original message",
        text_markdown_urled="original message",
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "original message"  # type: ignore[arg-type]


def test_extract_reply_quote_no_reply() -> None:
    """Message without reply should return None."""
    message = SimpleNamespace(
        text="message",
        from_user=SimpleNamespace(id=1),
        reply_to_message=None,
    )
    assert extract_reply_quote(message) is None  # type: ignore[arg-type]


def test_extract_reply_quote_different_user() -> None:
    """Reply to different user should return None."""
    replied_msg = SimpleNamespace(
        text="other user message",
        from_user=SimpleNamespace(id=99),
        caption=None,
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) is None  # type: ignore[arg-type]


def test_extract_reply_quote_media_with_caption() -> None:
    """Reply to photo with caption should extract caption."""
    replied_msg = SimpleNamespace(
        text=None,
        caption="photo caption",
        caption_markdown_urled="photo caption",
        from_user=SimpleNamespace(id=1),
        photo=[object()],
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "photo caption"  # type: ignore[arg-type]


def test_extract_reply_quote_media_without_caption() -> None:
    """Reply to photo without caption should use placeholder."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=[object()],
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "[Photo]"  # type: ignore[arg-type]


def test_extract_reply_quote_voice_placeholder() -> None:
    """Reply to voice should use [Voice message] placeholder."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=object(),
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "[Voice message]"  # type: ignore[arg-type]


def test_format_with_quote_single_line() -> None:
    """Quote formatting should prefix lines with > and separate with blank line."""
    result = format_with_quote("original", "new content")
    assert result == "> original\n\nnew content"


def test_format_with_quote_multiline() -> None:
    """Multi-line quotes should prefix each line with >."""
    result = format_with_quote("line 1\nline 2\nline 3", "reply")
    assert result == "> line 1\n> line 2\n> line 3\n\nreply"


def test_parse_note_render_payload_without_embeds() -> None:
    """Parser should keep plain notes as one text chunk."""
    payload = parse_note_render_payload("hello\nworld")
    assert payload == NoteRenderPayload(chunks=[TextChunk(text="hello\nworld")])


def test_parse_note_render_payload_with_embeds() -> None:
    """Parser should split text and embeds while preserving order."""
    payload = parse_note_render_payload(
        "head\n![[2026/attachments/a.jpg]]\nbody\n![[2026/attachments/b.ogg]]"
    )
    assert payload == NoteRenderPayload(
        chunks=[
            TextChunk(text="head\n"),
            AttachmentChunk(attachment_rel="2026/attachments/a.jpg"),
            TextChunk(text="\nbody\n"),
            AttachmentChunk(attachment_rel="2026/attachments/b.ogg"),
        ]
    )


def test_parse_note_render_payload_strips_alias_and_heading() -> None:
    """Parser should normalize embed path by removing alias/heading suffixes."""
    payload = parse_note_render_payload(
        "![[2026/attachments/a.jpg|preview]]\n![[2026/attachments/b.mp4#t=00:03]]"
    )
    assert payload == NoteRenderPayload(
        chunks=[
            AttachmentChunk(attachment_rel="2026/attachments/a.jpg"),
            TextChunk(text="\n"),
            AttachmentChunk(attachment_rel="2026/attachments/b.mp4"),
        ]
    )


def test_format_with_quote_empty_lines() -> None:
    """Empty lines in quotes should become standalone >."""
    result = format_with_quote("line 1\n\nline 3", "content")
    assert result == "> line 1\n>\n> line 3\n\ncontent"


def test_extract_reply_quote_video_placeholder() -> None:
    """Reply to video should use [Video] placeholder."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=object(),
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "[Video]"  # type: ignore[arg-type]


def test_extract_reply_quote_video_note_placeholder() -> None:
    """Reply to video note should use [Video note] placeholder."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=object(),
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "[Video note]"  # type: ignore[arg-type]


def test_extract_reply_quote_location_placeholder() -> None:
    """Reply to location should use [Location] placeholder."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=object(),
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "[Location]"  # type: ignore[arg-type]


def test_extract_reply_quote_no_content() -> None:
    """Reply to message with no extractable content should return None."""
    replied_msg = SimpleNamespace(
        text=None,
        caption=None,
        from_user=SimpleNamespace(id=1),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) is None  # type: ignore[arg-type]


def test_extract_reply_quote_missing_from_user() -> None:
    """Reply without from_user should return None."""
    replied_msg = SimpleNamespace(
        text="message",
        from_user=None,  # Missing from_user
        caption=None,
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="reply",
        from_user=SimpleNamespace(id=1),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) is None  # type: ignore[arg-type]


def test_extract_reply_quote_bot_reply() -> None:
    """Reply to a bot message should include the bot's message as a quote."""
    replied_msg = SimpleNamespace(
        text="On this day in 2023 you wrote...",
        text_markdown_urled="On this day in 2023 you wrote...",
        caption=None,
        from_user=SimpleNamespace(id=999, is_bot=True),
        photo=None,
        voice=None,
        video=None,
        video_note=None,
        location=None,
    )
    message = SimpleNamespace(
        text="my reply to the bot",
        from_user=SimpleNamespace(id=1, is_bot=False),
        reply_to_message=replied_msg,
    )
    assert extract_reply_quote(message) == "On this day in 2023 you wrote..."  # type: ignore[arg-type]
