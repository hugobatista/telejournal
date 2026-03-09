"""Unit tests for pure bot logic helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_journal_bot.logic import (
    effective_note_datetime,
    parse_setdate_args,
    should_prompt_for_mood,
)


def test_parse_setdate_without_time_uses_current_time() -> None:
    """Date-only input should preserve current time component."""
    now = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    parsed = parse_setdate_args(["2026-03-01"], now)
    assert parsed == datetime(2026, 3, 1, 18, 34, 42, tzinfo=UTC)


def test_parse_setdate_with_time() -> None:
    """Date and explicit time should be parsed exactly."""
    now = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    parsed = parse_setdate_args(["2026-03-01", "11:05:00"], now)
    assert parsed == datetime(2026, 3, 1, 11, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "args",
    [[], ["bad"], ["2026-03-07", "bad"], ["2026-03-07", "11:00:00", "x"]],
)
def test_parse_setdate_invalid_inputs_raise(args: list[str]) -> None:
    """Invalid argument combinations should raise ValueError."""
    now = datetime(2026, 3, 7, 18, 34, 42, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_setdate_args(args, now)


def test_should_prompt_for_mood_when_threshold_passed() -> None:
    """Prompt should trigger once interval has passed and no mood exists."""
    now = datetime.now(UTC)
    assert should_prompt_for_mood(
        note_has_entry=True,
        note_has_mood=False,
        now=now,
        last_prompted_at=None,
        reminder_interval_hours=4,
    )


def test_should_not_prompt_if_mood_already_set() -> None:
    """Mood reminders must not trigger when mood already exists."""
    now = datetime.now(UTC)
    assert not should_prompt_for_mood(
        note_has_entry=True,
        note_has_mood=True,
        now=now,
        last_prompted_at=None,
        reminder_interval_hours=4,
    )


def test_effective_note_datetime_without_override() -> None:
    """Without override the current datetime should pass through unchanged."""
    now = datetime(2026, 3, 7, 12, 0, 0, tzinfo=UTC)
    assert effective_note_datetime(None, now) == now


def test_effective_note_datetime_with_override() -> None:
    """Override should swap date while preserving time component."""
    now = datetime(2026, 3, 7, 12, 0, 0, tzinfo=UTC)
    override = datetime(2026, 3, 1, 1, 1, 1, tzinfo=UTC)
    assert effective_note_datetime(override, now) == datetime(
        2026,
        3,
        1,
        12,
        0,
        0,
        tzinfo=UTC,
    )


def test_should_not_prompt_when_no_entry() -> None:
    """Notes without journal entries should not trigger reminders."""
    now = datetime.now(UTC)
    assert not should_prompt_for_mood(
        note_has_entry=False,
        note_has_mood=False,
        now=now,
        last_prompted_at=None,
    )


def test_should_prompt_without_previous_prompt_when_no_mood() -> None:
    """Any note with entries and no mood should prompt when not prompted yet."""
    now = datetime.now(UTC)
    assert should_prompt_for_mood(
        note_has_entry=True,
        note_has_mood=False,
        now=now,
        last_prompted_at=None,
    )


def test_should_not_prompt_too_soon_after_prompt() -> None:
    """Re-prompts should wait for interval when mood is still missing."""
    now = datetime.now(UTC)
    assert not should_prompt_for_mood(
        note_has_entry=True,
        note_has_mood=False,
        now=now,
        last_prompted_at=now - timedelta(hours=1),
    )
