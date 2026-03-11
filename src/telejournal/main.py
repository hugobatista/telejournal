"""Application entrypoint for the Telegram journal bot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application

from telejournal import __version__
from telejournal.bot import JournalBot
from telejournal.config import Settings, load_settings
from telejournal.logger import setup_default_logging, setup_logging
from telejournal.output_handler import OutputHandler

__all__ = ["app", "main", "run"]

app = typer.Typer(
    help="Telegram bot that journals private messages into your vault."
)


def _resolve_run_config_path(config_path: Path | None) -> Path | None:
    """Resolve CLI config path, defaulting to local config.yaml when present."""
    if config_path is not None:
        return config_path

    default_path = Path("config.yaml")
    if default_path.exists():
        return default_path
    return None


def _build_cli_overrides(
    telegram_token: str | None,
    vault_root: Path | None,
    allowed_user_ids: str | None,
    log_level: str | None,
    message_timestamp_window_seconds: int | None,
    secure_file_permissions: bool | None,
) -> dict[str, Any]:
    """Build CLI override mapping from run command arguments."""
    return {
        "telegram_token": telegram_token,
        "vault_root": str(vault_root) if vault_root is not None else None,
        "allowed_user_ids": allowed_user_ids,
        "log_level": log_level,
        "message_timestamp_window_seconds": message_timestamp_window_seconds,
        "secure_file_permissions": secure_file_permissions,
    }


def _start_bot(telegram_token: str, settings: Settings) -> None:
    """Create and run the Telegram polling application."""
    app_instance = Application.builder().token(telegram_token).build()

    journal_bot = JournalBot(settings)
    journal_bot.register_handlers(app_instance)
    if app_instance.job_queue is None:
        raise RuntimeError("Job queue is unavailable; install job-queue extras")
    journal_bot.register_jobs(app_instance.job_queue)

    app_instance.run_polling(allowed_updates=Update.ALL_TYPES)


@app.command("run")
def run_command(
    config: Path | None = typer.Argument(
        None,
        exists=False,
        file_okay=True,
        dir_okay=False,
        help="Optional YAML config path. Defaults to ./config.yaml when present.",
    ),
    telegram_token: str | None = typer.Option(
        None,
        "--telegram-token",
        help="Telegram bot token.",
    ),
    vault_root: Path | None = typer.Option(
        None,
        "--vault-root",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=False,
        help="Root of your Obsidian vault.",
    ),
    allowed_user_ids: str | None = typer.Option(
        None,
        "--allowed-user-ids",
        help="Comma-separated Telegram user IDs.",
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    ),
    message_timestamp_window_seconds: int | None = typer.Option(
        None,
        "--message-timestamp-window-seconds",
        help="Message grouping window in seconds.",
    ),
    secure_file_permissions: bool | None = typer.Option(
        None,
        "--secure-file-permissions/--no-secure-file-permissions",
        help="Enable secure file permissions for created files and dirs.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable verbose logging and print startup details to console.",
    ),
) -> None:
    """Run the Telegram journal bot."""
    load_dotenv()
    setup_default_logging()
    output = OutputHandler()

    resolved_config = _resolve_run_config_path(config)
    cli_overrides = _build_cli_overrides(
        telegram_token,
        vault_root,
        allowed_user_ids,
        log_level,
        message_timestamp_window_seconds,
        secure_file_permissions,
    )

    try:
        settings = load_settings(
            config_path=resolved_config,
            cli_overrides=cli_overrides,
        )
    except (FileNotFoundError, ValueError) as exc:
        output.error(f"Configuration error: {exc}", echo=True)
        raise typer.Exit(1) from exc

    setup_logging(settings.log_level, verbose=verbose)
    if verbose:
        output.info(f"Telejournal v{__version__} starting", echo=True)
        output.info(f"Vault root: {settings.vault_root}", echo=True)
        output.info(f"Log level: {settings.log_level}", echo=True)

    _start_bot(settings.telegram_token, settings)


@app.command("version")
def version_command() -> None:
    """Show application version."""
    typer.echo(f"telejournal {__version__}")


@app.command("help")
def help_command(ctx: typer.Context) -> None:
    """Show top-level help information."""
    typer.echo(ctx.find_root().get_help())


def main() -> None:
    """Backward-compatible wrapper that executes the Typer CLI."""
    app()


def run() -> None:
    """Console script wrapper that executes the Typer CLI."""
    main()


if __name__ == "__main__":  # pragma: no cover
    run()
