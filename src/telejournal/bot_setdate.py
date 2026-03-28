"""Guided /setdate conversation flow with inline calendar picker."""

from __future__ import annotations

from calendar import month_name, monthrange
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from telejournal.logic import parse_setdate_args

SETDATE_CALLBACK_PREFIX = "setdatecal:"
SETDATE_AWAIT_DATE = 1


class SetDateFlowService:
    """Handle /setdate direct usage, text-guided flow, and calendar callbacks."""

    def __init__(
        self,
        *,
        is_private_and_authorized: Callable[[Update], bool],
        chat_data_resolver: Callable[[ContextTypes.DEFAULT_TYPE], dict[str, Any]],
        setdate_with_args: Callable[
            [Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]
        ],
        override_date_key: str,
    ) -> None:
        """Store setdate flow dependencies and chat-data key names."""
        self._is_private_and_authorized = is_private_and_authorized
        self._chat_data_resolver = chat_data_resolver
        self._setdate_with_args = setdate_with_args
        self._override_date_key = override_date_key

    async def start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        """Start /setdate flow or delegate to direct argument-based parsing."""
        if not self._is_private_and_authorized(update):
            return ConversationHandler.END

        if context.args:
            await self._setdate_with_args(update, context)
            return ConversationHandler.END

        if update.effective_message:
            now = datetime.now(UTC)
            await update.effective_message.reply_text(
                "Choose a date for the override.",
                reply_markup=self._calendar_keyboard(now.year, now.month),
            )
        return ConversationHandler.END

    async def handle_text_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        """Handle manual YYYY-MM-DD input while in /setdate conversation."""
        if not self._is_private_and_authorized(update):
            return ConversationHandler.END

        if not update.effective_message or not isinstance(
            update.effective_message.text, str
        ):
            return SETDATE_AWAIT_DATE

        date_input = update.effective_message.text.strip()
        now = datetime.now(UTC)
        try:
            override_dt = parse_setdate_args([date_input], now)
        except ValueError:
            await update.effective_message.reply_text(
                "❌ Invalid date. Use YYYY-MM-DD, for example: 2026-03-20"
            )
            return SETDATE_AWAIT_DATE

        chat_data = self._chat_data_resolver(context)
        chat_data[self._override_date_key] = override_dt
        await update.effective_message.reply_text(
            f"Date override set to {override_dt.strftime('%Y-%m-%d')} (UTC)."
        )
        return ConversationHandler.END

    async def handle_calendar_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        """Handle month navigation and date selection from inline calendar."""
        if not self._is_private_and_authorized(update):
            return ConversationHandler.END

        query = update.callback_query
        if query is None or not isinstance(query.data, str):
            return SETDATE_AWAIT_DATE

        data = query.data
        if not data.startswith(SETDATE_CALLBACK_PREFIX):
            return SETDATE_AWAIT_DATE

        payload = data.removeprefix(SETDATE_CALLBACK_PREFIX)
        if payload == "noop":
            await query.answer()
            return SETDATE_AWAIT_DATE

        if payload == "cancel":
            await query.answer()
            await query.edit_message_text("Date override canceled.")
            return ConversationHandler.END

        if payload.startswith("nav:"):
            month_token = payload.removeprefix("nav:")
            try:
                month_dt = datetime.strptime(month_token, "%Y-%m")
            except ValueError:
                await query.answer()
                return SETDATE_AWAIT_DATE

            await query.answer()
            await query.edit_message_text(
                "Choose a date for the override, or send YYYY-MM-DD manually.",
                reply_markup=self._calendar_keyboard(month_dt.year, month_dt.month),
            )
            return SETDATE_AWAIT_DATE

        if payload.startswith("pick:"):
            date_token = payload.removeprefix("pick:")
            now = datetime.now(UTC)
            try:
                override_dt = parse_setdate_args([date_token], now)
            except ValueError:
                await query.answer()
                return SETDATE_AWAIT_DATE

            chat_data = self._chat_data_resolver(context)
            chat_data[self._override_date_key] = override_dt
            await query.answer()
            await query.edit_message_text(
                f"Date override set to {override_dt.strftime('%Y-%m-%d')} (UTC)."
            )
            return ConversationHandler.END

        await query.answer()
        return SETDATE_AWAIT_DATE

    @staticmethod
    def _calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
        """Build an inline calendar keyboard for one year-month pair."""
        _, days_in_month = monthrange(year, month)

        rows: list[list[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(
                    f"{month_name[month]} {year}",
                    callback_data=f"{SETDATE_CALLBACK_PREFIX}noop",
                )
            ],
            [
                InlineKeyboardButton(
                    "Mo", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
                InlineKeyboardButton(
                    "Tu", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
                InlineKeyboardButton(
                    "We", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
                InlineKeyboardButton(
                    "Th", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
                InlineKeyboardButton(
                    "Fr", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
                InlineKeyboardButton(
                    "Sa", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
                InlineKeyboardButton(
                    "Su", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                ),
            ],
        ]

        first_weekday, _ = monthrange(year, month)
        week: list[InlineKeyboardButton] = [
            InlineKeyboardButton(" ", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop")
            for _ in range(first_weekday)
        ]
        for day in range(1, days_in_month + 1):
            week.append(
                InlineKeyboardButton(
                    f"{day}",
                    callback_data=(
                        f"{SETDATE_CALLBACK_PREFIX}pick:{year:04d}-{month:02d}-{day:02d}"
                    ),
                )
            )
            if len(week) == 7:
                rows.append(week)
                week = []

        if week:
            while len(week) < 7:
                week.append(
                    InlineKeyboardButton(
                        " ", callback_data=f"{SETDATE_CALLBACK_PREFIX}noop"
                    )
                )
            rows.append(week)

        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        rows.append(
            [
                InlineKeyboardButton(
                    "◀",
                    callback_data=(
                        f"{SETDATE_CALLBACK_PREFIX}nav:{prev_year:04d}-{prev_month:02d}"
                    ),
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"{SETDATE_CALLBACK_PREFIX}cancel",
                ),
                InlineKeyboardButton(
                    "▶",
                    callback_data=(
                        f"{SETDATE_CALLBACK_PREFIX}nav:{next_year:04d}-{next_month:02d}"
                    ),
                ),
            ]
        )

        return InlineKeyboardMarkup(rows)
