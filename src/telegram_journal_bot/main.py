"""Application entrypoint for the Telegram journal bot."""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application

from telegram_journal_bot.bot import JournalBot
from telegram_journal_bot.config import load_settings

__all__ = ["Application", "main", "run"]


def _configure_logging(log_level: str) -> None:
    """Configure application logging level and format."""
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",

    )

    # set higher logging level for httpx to avoid all GET and POST requests being logged
    logging.getLogger("httpx").setLevel(logging.WARNING)





def main() -> None:
    """Create and run the Telegram polling application."""
    load_dotenv()
    settings = load_settings()
    _configure_logging(settings.log_level)

    app = Application.builder().token(settings.telegram_token).build()

    journal_bot = JournalBot(settings)
    journal_bot.register_handlers(app)
    if app.job_queue is None:
        raise RuntimeError("Job queue is unavailable; install job-queue extras")
    journal_bot.register_jobs(app.job_queue)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


def run() -> None:
    """Synchronous console script wrapper."""
    main()


if __name__ == "__main__":  # pragma: no cover
    run()
