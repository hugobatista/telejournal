"""Application entrypoint for the Telegram journal bot."""

from __future__ import annotations

import getpass
import inspect
import shutil
import sys
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

app = typer.Typer(help="Telegram bot that journals private messages into your vault.")


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


def _get_current_user() -> str:
    """Get the current system user."""
    return getpass.getuser()


def _get_telejournal_executable_path() -> str:
    """Get the path to the current telejournal executable."""
    # Get sys.executable (Python interpreter path)
    if hasattr(sys, "argv") and sys.argv[0]:
        # Try to find 'telejournal' in PATH first (when installed via pip/pipx)
        telejournal_path = shutil.which("telejournal")
        if telejournal_path:
            return telejournal_path

    # Fallback to the current module's executable
    return str(Path(sys.executable).parent / "telejournal")


def _build_systemd_service_content(
    user: str,
    working_directory: Path,
    environment_file: Path,
    execstart: str,
) -> str:
    """Build systemd service file content."""
    return f"""[Unit]
Description=Telegram Journal Bot
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={working_directory}
EnvironmentFile={environment_file}
ExecStart={execstart}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _load_env_from_cwd() -> None:
    """Load ``.env`` from the current directory with test-friendly fallback."""
    try:
        signature = inspect.signature(load_dotenv)
    except (TypeError, ValueError):
        load_dotenv()
        return

    if len(signature.parameters) == 0:
        load_dotenv()
        return

    load_dotenv(Path.cwd() / ".env")


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
    # Prefer loading ``.env`` from the working directory for pipx installs.
    # Fall back to a zero-arg call when tests monkeypatch ``load_dotenv``.
    _load_env_from_cwd()
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


@app.command("install-service")
def install_service_command(
    user: str | None = typer.Option(
        None,
        "--user",
        help="User account to run the service as. Defaults to current user.",
    ),
    working_directory: Path | None = typer.Option(
        None,
        "--working-directory",
        file_okay=False,
        dir_okay=True,
        help="Working directory for the service. Defaults to ~/obsidian-journal.",
    ),
    environment_file: Path | None = typer.Option(
        None,
        "--environment-file",
        file_okay=True,
        dir_okay=False,
        help="Environment file path. Defaults to ~/.env.",
    ),
    execstart: str | None = typer.Option(
        None,
        "--execstart",
        help="ExecStart command. Defaults to the current telejournal instance path.",
    ),
    output_path: Path = typer.Option(
        Path("/etc/systemd/system/telejournal.service"),
        "--output-path",
        "-o",
        file_okay=True,
        dir_okay=False,
        help="Where to write the systemd service file.",
    ),
) -> None:
    """Generate a systemd service file for telejournal.

    This command creates a service file that allows telejournal to run as a
    background service managed by systemd. After creating the file, you'll need
    to run:

        sudo systemctl daemon-reload
        sudo systemctl enable telejournal.service
        sudo systemctl start telejournal.service
    """
    output = OutputHandler()

    # Resolve defaults
    resolved_user = user or _get_current_user()
    home_dir = Path.home()

    if working_directory is None:
        resolved_working_dir = home_dir / "obsidian-journal"
    else:
        resolved_working_dir = working_directory.expanduser()

    if environment_file is None:
        resolved_env_file = home_dir / ".env"
    else:
        resolved_env_file = environment_file.expanduser()

    if execstart is None:
        telejournal_path = _get_telejournal_executable_path()
        resolved_execstart = f"{telejournal_path} run"
    else:
        resolved_execstart = execstart

    # Build service content
    service_content = _build_systemd_service_content(
        user=resolved_user,
        working_directory=resolved_working_dir,
        environment_file=resolved_env_file,
        execstart=resolved_execstart,
    )

    # Show the user what we're about to write
    output.info("Systemd service file configuration:", echo=True)
    output.info(f"  User: {resolved_user}", echo=True)
    output.info(f"  WorkingDirectory: {resolved_working_dir}", echo=True)
    output.info(f"  EnvironmentFile: {resolved_env_file}", echo=True)
    output.info(f"  ExecStart: {resolved_execstart}", echo=True)
    output.info(f"  Output: {output_path}", echo=True)
    output.info("", echo=True)

    # Write the service file
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(service_content, encoding="utf-8")
        output_path.chmod(0o644)

        output.info(
            f"✓ Service file created at {output_path}",
            echo=True,
        )
        output.info("", echo=True)
        output.info("Next steps:", echo=True)
        output.info("  1. sudo systemctl daemon-reload", echo=True)
        output.info("  2. sudo systemctl enable telejournal.service", echo=True)
        output.info("  3. sudo systemctl start telejournal.service", echo=True)
        output.info("", echo=True)
        output.info(
            "Check service status with: sudo systemctl status telejournal.service",
            echo=True,
        )
    except (IOError, OSError) as exc:
        output.error(
            f"Failed to write service file: {exc}",
            echo=True,
        )
        raise typer.Exit(1) from exc


def main() -> None:
    """Backward-compatible wrapper that executes the Typer CLI."""
    app()


def run() -> None:
    """Console script wrapper that executes the Typer CLI."""
    main()


if __name__ == "__main__":  # pragma: no cover
    run()
