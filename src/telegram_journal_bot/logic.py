"""Pure logic helpers used by handlers and tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def parse_setdate_args(args: list[str], now: datetime) -> datetime:
    """Parse `/setdate` arguments into a UTC datetime.

    Accepted forms:
    - YYYY-MM-DD
    - YYYY-MM-DD HH:MM:SS
    """
    if not args:
        raise ValueError("missing date")

    date_part = args[0]
    parsed_date = datetime.strptime(date_part, "%Y-%m-%d").date()

    if len(args) == 1:
        return datetime.combine(parsed_date, now.timetz()).replace(tzinfo=UTC)

    if len(args) == 2:
        parsed_time = datetime.strptime(args[1], "%H:%M:%S").time()
        return datetime.combine(parsed_date, parsed_time).replace(tzinfo=UTC)

    raise ValueError("too many arguments")


def effective_note_datetime(
    override_date: datetime | None,
    now: datetime,
) -> datetime:
    """Return note datetime using override date while preserving current UTC time."""
    if override_date is None:
        return now

    return datetime.combine(
        override_date.date(),
        now.timetz(),
    ).replace(tzinfo=UTC)


def should_prompt_for_mood(
    *,
    note_has_entry: bool,
    note_has_mood: bool,
    now: datetime,
    last_prompted_at: datetime | None,
    reminder_interval_hours: int = 4,
) -> bool:
    """Decide whether a mood prompt should be sent for the chat."""
    if not note_has_entry or note_has_mood:
        return False

    threshold = timedelta(hours=reminder_interval_hours)
    if last_prompted_at is None:
        return True

    return now - last_prompted_at >= threshold


def today_utc() -> date:
    """Return the current UTC date."""
    return datetime.now(UTC).date()
