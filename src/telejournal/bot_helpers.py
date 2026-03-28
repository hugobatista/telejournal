"""Pure helper functions for Telegram bot command parsing and UI widgets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telejournal.formatting import MOOD_LABELS

MAX_TELEGRAM_TEXT_LEN = 4096
MOOD_CALLBACK_PREFIX = "mood:"
TAG_CALLBACK_PREFIX = "tag:"
DELETE_CALLBACK_PREFIX = "delete:"
HISTORY_CALLBACK_PREFIX = "history:"


def _parse_tags_from_args(args: list[str]) -> set[str]:
    """Parse tag names from /tags args, supporting commas and spaces."""
    parsed: set[str] = set()
    for raw in args:
        for part in raw.split(","):
            tag = part.strip().lower()
            if tag:
                parsed.add(tag)
    return parsed


def _parse_iso_date(raw_date: str) -> datetime:
    """Parse ``YYYY-MM-DD`` to UTC midnight with sane date bounds."""
    parsed_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    result = datetime.combine(parsed_date, datetime.min.time()).replace(tzinfo=UTC)

    now = datetime.now(UTC)
    min_date = now - timedelta(days=730)
    max_date = now + timedelta(days=365)

    if result < min_date or result > max_date:
        raise ValueError(
            f"Date {raw_date} is outside allowed range "
            f"({min_date.date()} to {max_date.date()})"
        )

    return result


def _truncate_message(text: str, max_len: int = MAX_TELEGRAM_TEXT_LEN) -> str:
    """Trim long output to fit Telegram message length limits."""
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 5]}\n..."


def _chunk_text(text: str, chunk_size: int = MAX_TELEGRAM_TEXT_LEN) -> list[str]:
    """Split long text into Telegram-sized chunks using line breaks when possible."""
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > chunk_size:
        split_at = remaining.rfind("\n", 0, chunk_size)
        if split_at <= 0:
            split_at = chunk_size

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:chunk_size]
            split_at = len(chunk)
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)
    return chunks


def _mood_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for mood selection."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{MOOD_CALLBACK_PREFIX}{value}")
        for value, label in MOOD_LABELS.items()
    ]
    return InlineKeyboardMarkup([buttons])


def _tags_keyboard(
    current_tags: set[str],
    tag_choices: tuple[str, ...],
) -> InlineKeyboardMarkup:
    """Build inline keyboard for tags add/remove interactions."""
    buttons: list[list[InlineKeyboardButton]] = []
    for tag in tag_choices:
        if tag in current_tags:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"✅ {tag}",
                        callback_data=f"{TAG_CALLBACK_PREFIX}remove:{tag}",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"➕ {tag}",
                        callback_data=f"{TAG_CALLBACK_PREFIX}add:{tag}",
                    )
                ]
            )
    return InlineKeyboardMarkup(buttons)


def _delete_confirmation_keyboard(action: str, note_date: str) -> InlineKeyboardMarkup:
    """Build confirmation keyboard for delete actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Confirm",
                    callback_data=(
                        f"{DELETE_CALLBACK_PREFIX}confirm:{action}:{note_date}"
                    ),
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"{DELETE_CALLBACK_PREFIX}cancel",
                ),
            ]
        ]
    )


def _history_render_keyboard(action: str, date_str: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for choosing note-only vs rendered output."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Notes only",
                    callback_data=(f"{HISTORY_CALLBACK_PREFIX}{action}:raw:{date_str}"),
                ),
                InlineKeyboardButton(
                    "Rendered",
                    callback_data=(
                        f"{HISTORY_CALLBACK_PREFIX}{action}:rendered:{date_str}"
                    ),
                ),
            ]
        ]
    )
