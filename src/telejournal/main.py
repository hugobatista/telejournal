"""Application entrypoint for the Telegram journal bot."""

from __future__ import annotations

import asyncio
import getpass
import inspect
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ConversationHandler

from telejournal import __version__
from telejournal.bot import JournalBot
from telejournal.config import Settings, load_settings
from telejournal.logger import setup_default_logging, setup_logging
from telejournal.output_handler import OutputHandler

__all__ = ["app", "main", "run"]

SETTINGS_BOT_DATA_KEY = "settings"

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
    storage_provider: str | None,
    obsidian_vault_root: Path | None,
    obsidian_vault_secure_file_permissions: bool | None,
    github_owner: str | None,
    github_repo: str | None,
    github_branch: str | None,
    github_token: str | None,
    github_path_prefix: str | None,
    github_api_base_url: str | None,
    github_batch_window_seconds: int | None,
    onedrive_tenant_id: str | None,
    onedrive_client_id: str | None,
    onedrive_client_secret: str | None,
    onedrive_root_path: str | None,
    onedrive_api_base_url: str | None,
    onedrive_batch_window_seconds: int | None,
    onedrive_access_token: str | None,
    onedrive_refresh_token: str | None,
    onedrive_token_expires_at_utc: str | None,
        google_drive_client_id: str | None = None,
        google_drive_client_secret: str | None = None,
        google_drive_folder_id: str | None = None,
        google_drive_batch_window_seconds: int | None = None,
        google_drive_access_token: str | None = None,
        google_drive_refresh_token: str | None = None,
        google_drive_token_expires_at_utc: str | None = None,
    allowed_user_ids: str | None = None,
    log_level: str | None = None,
    message_timestamp_window_seconds: int | None = None,
    daily_brief_time_utc: str | None = None,
) -> dict[str, Any]:
    """Build CLI override mapping from run command arguments."""
    return {
        "telegram_token": telegram_token,
        "storage": {
            "provider": storage_provider,
            "obsidian_vault": {
                "root": (
                    str(obsidian_vault_root)
                    if obsidian_vault_root is not None
                    else None
                ),
                "secure_file_permissions": (obsidian_vault_secure_file_permissions),
            },
            "github_repo": {
                "owner": github_owner,
                "repo": github_repo,
                "branch": github_branch,
                "token": github_token,
                "path_prefix": github_path_prefix,
                "api_base_url": github_api_base_url,
                "batch_window_seconds": github_batch_window_seconds,
            },
            "onedrive": {
                "tenant_id": onedrive_tenant_id,
                "client_id": onedrive_client_id,
                "client_secret": onedrive_client_secret,
                "root_path": onedrive_root_path,
                "api_base_url": onedrive_api_base_url,
                "batch_window_seconds": onedrive_batch_window_seconds,
                "access_token": onedrive_access_token,
                "refresh_token": onedrive_refresh_token,
                "token_expires_at_utc": onedrive_token_expires_at_utc,
            },
            "google_drive": {
                "client_id": google_drive_client_id,
                "client_secret": google_drive_client_secret,
                "folder_id": google_drive_folder_id,
                "batch_window_seconds": google_drive_batch_window_seconds,
                "access_token": google_drive_access_token,
                "refresh_token": google_drive_refresh_token,
                "token_expires_at_utc": google_drive_token_expires_at_utc,
            },
        },
        "allowed_user_ids": allowed_user_ids,
        "log_level": log_level,
        "message_timestamp_window_seconds": message_timestamp_window_seconds,
        "daily_brief_time_utc": daily_brief_time_utc,
    }


def _fallback_command_description(command: str) -> str:
    """Build a readable menu description from a command token."""
    normalized = command.replace("_", " ").replace("-", " ").strip()
    if not normalized:
        return "Use this command"
    return f"{normalized.capitalize()} the bot"


def _command_description_from_handler(
    handler: CommandHandler,  # type: ignore[type-arg]
    command: str,
) -> str:
    """Resolve command description from callback docstring or fallback text."""
    callback = getattr(handler, "callback", None)
    if callable(callback):
        doc = inspect.getdoc(callback)
        if doc:
            first_sentence = doc.strip().split("\n", maxsplit=1)[0].strip()
            if first_sentence:
                return first_sentence.rstrip(".")
    return _fallback_command_description(command)


def _build_bot_commands(
    application: Application,  # type: ignore[type-arg]
) -> list[BotCommand]:
    """Build unique command menu items from registered command handlers."""
    commands: list[BotCommand] = []
    seen_commands: set[str] = set()

    def _append_from_handler(handler: object) -> None:
        if isinstance(handler, CommandHandler):
            for command in handler.commands:
                if command in seen_commands:
                    continue
                seen_commands.add(command)
                commands.append(
                    BotCommand(
                        command=command,
                        description=_command_description_from_handler(
                            handler,
                            command,
                        ),
                    )
                )
            return

        if isinstance(handler, ConversationHandler):
            for entry in handler.entry_points:
                _append_from_handler(entry)
            for fallbacks in handler.fallbacks:
                _append_from_handler(fallbacks)
            for state_handlers in handler.states.values():
                for state_handler in state_handlers:
                    _append_from_handler(state_handler)

    for handlers in application.handlers.values():
        for handler in handlers:
            _append_from_handler(handler)

    return commands


async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    """Register command menu items after all handlers are attached."""
    settings = application.bot_data.get(SETTINGS_BOT_DATA_KEY)
    if isinstance(settings, Settings) and not settings.bot_menu_enabled:
        await application.bot.delete_my_commands()
        return

    commands = _build_bot_commands(application)
    await application.bot.set_my_commands(commands)


def _start_bot(telegram_token: str, settings: Settings) -> None:
    """Create and run the Telegram polling application."""
    app_instance = (
        Application.builder().token(telegram_token).post_init(post_init).build()
    )
    app_instance.bot_data[SETTINGS_BOT_DATA_KEY] = settings

    journal_bot = JournalBot(settings)
    journal_bot.register_handlers(app_instance)
    if app_instance.job_queue is None:
        raise RuntimeError("Job queue is unavailable; install job-queue extras")
    journal_bot.register_jobs(app_instance.job_queue)

    try:
        app_instance.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        # Best-effort durability: flush queued storage writes before process exits
        # (for example, SIGTERM during container shutdown).
        asyncio.run(journal_bot.shutdown())


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
    storage_provider: str | None = typer.Option(
        None,
        "--storage-provider",
        help=(
            "Storage provider: obsidian_vault, github_repo, onedrive, "
            "or google_drive."
        ),
    ),
    obsidian_vault_root: Path | None = typer.Option(
        None,
        "--obsidian-vault-root",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=False,
        help="Root path for obsidian_vault storage provider.",
    ),
    obsidian_vault_secure_file_permissions: bool | None = typer.Option(
        None,
        "--obsidian-vault-secure-file-permissions/"
        "--no-obsidian-vault-secure-file-permissions",
        help=(
            "Enable restrictive permissions (0o700/0o600) for "
            "obsidian_vault storage files and directories."
        ),
    ),
    github_owner: str | None = typer.Option(
        None,
        "--github-owner",
        help="GitHub owner for github_repo storage provider.",
    ),
    github_repo: str | None = typer.Option(
        None,
        "--github-repo",
        help="GitHub repository name for github_repo storage provider.",
    ),
    github_branch: str | None = typer.Option(
        None,
        "--github-branch",
        help="Git branch used by github_repo storage provider.",
    ),
    github_token: str | None = typer.Option(
        None,
        "--github-token",
        help="GitHub token for github_repo storage provider.",
    ),
    github_path_prefix: str | None = typer.Option(
        None,
        "--github-path-prefix",
        help="Optional repository sub-path where journal files are stored.",
    ),
    github_api_base_url: str | None = typer.Option(
        None,
        "--github-api-base-url",
        help="GitHub API base URL (defaults to https://api.github.com).",
    ),
    github_batch_window_seconds: int | None = typer.Option(
        None,
        "--github-batch-window-seconds",
        help=(
            "Flush pending github_repo writes in bursts every N seconds "
            "(default: 60)."
        ),
    ),
    onedrive_tenant_id: str | None = typer.Option(
        None,
        "--onedrive-tenant-id",
        help="Microsoft tenant ID for onedrive storage provider.",
    ),
    onedrive_client_id: str | None = typer.Option(
        None,
        "--onedrive-client-id",
        help="Microsoft app client ID for onedrive storage provider.",
    ),
    onedrive_client_secret: str | None = typer.Option(
        None,
        "--onedrive-client-secret",
        help="Microsoft app client secret for onedrive storage provider.",
    ),
    onedrive_root_path: str | None = typer.Option(
        None,
        "--onedrive-root-path",
        help=(
            "Root folder path in OneDrive for telejournal data "
            "(for example, Apps/telejournal)."
        ),
    ),
    onedrive_api_base_url: str | None = typer.Option(
        None,
        "--onedrive-api-base-url",
        help="Microsoft Graph API base URL.",
    ),
    onedrive_batch_window_seconds: int | None = typer.Option(
        None,
        "--onedrive-batch-window-seconds",
        help=(
            "Flush pending onedrive writes in bursts every N seconds " "(default: 60)."
        ),
    ),
    onedrive_access_token: str | None = typer.Option(
        None,
        "--onedrive-access-token",
        help="Cached OneDrive access token.",
    ),
    onedrive_refresh_token: str | None = typer.Option(
        None,
        "--onedrive-refresh-token",
        help="Cached OneDrive refresh token.",
    ),
    onedrive_token_expires_at_utc: str | None = typer.Option(
        None,
        "--onedrive-token-expires-at-utc",
        help=("Access token expiry in UTC format YYYY-MM-DDTHH:MM:SSZ."),
    ),
    google_drive_client_id: str | None = typer.Option(
        None,
        "--google-drive-client-id",
        help="Google OAuth app client ID for google_drive storage provider.",
    ),
    google_drive_client_secret: str | None = typer.Option(
        None,
        "--google-drive-client-secret",
        help="Google OAuth app client secret for google_drive provider.",
    ),
    google_drive_folder_id: str | None = typer.Option(
        None,
        "--google-drive-folder-id",
        help="Target Google Drive folder ID for telejournal files.",
    ),
    google_drive_batch_window_seconds: int | None = typer.Option(
        None,
        "--google-drive-batch-window-seconds",
        help="Flush pending google_drive writes every N seconds (default: 60).",
    ),
    google_drive_access_token: str | None = typer.Option(
        None,
        "--google-drive-access-token",
        help="Cached Google Drive access token.",
    ),
    google_drive_refresh_token: str | None = typer.Option(
        None,
        "--google-drive-refresh-token",
        help="Cached Google Drive refresh token.",
    ),
    google_drive_token_expires_at_utc: str | None = typer.Option(
        None,
        "--google-drive-token-expires-at-utc",
        help="Google access token expiry in UTC format YYYY-MM-DDTHH:MM:SSZ.",
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
    daily_brief_time_utc: str | None = typer.Option(
        None,
        "--daily-brief-time-utc",
        help="Daily UTC time for on-this-day brief (HH:MM, HH:MM:SS, or 0 to disable).",
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
        storage_provider,
        obsidian_vault_root,
        obsidian_vault_secure_file_permissions,
        github_owner,
        github_repo,
        github_branch,
        github_token,
        github_path_prefix,
        github_api_base_url,
        github_batch_window_seconds,
        onedrive_tenant_id,
        onedrive_client_id,
        onedrive_client_secret,
        onedrive_root_path,
        onedrive_api_base_url,
        onedrive_batch_window_seconds,
        onedrive_access_token,
        onedrive_refresh_token,
        onedrive_token_expires_at_utc,
        google_drive_client_id,
        google_drive_client_secret,
        google_drive_folder_id,
        google_drive_batch_window_seconds,
        google_drive_access_token,
        google_drive_refresh_token,
        google_drive_token_expires_at_utc,
        allowed_user_ids,
        log_level,
        message_timestamp_window_seconds,
        daily_brief_time_utc,
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
        if settings.storage_provider == "obsidian_vault":
            output.info(f"Storage: obsidian_vault ({settings.vault_root})", echo=True)
        elif settings.storage_provider == "github_repo":
            output.info(
                (
                    "Storage: github_repo "
                    f"({settings.github_owner}/{settings.github_repo}"
                    f"@{settings.github_branch})"
                ),
                echo=True,
            )
            output.info(
                ("GitHub batch window: " f"{settings.github_batch_window_seconds}s"),
                echo=True,
            )
        elif settings.storage_provider == "onedrive":
            output.info(
                (
                    "Storage: onedrive "
                    f"({settings.onedrive_root_path}, tenant={settings.onedrive_tenant_id})"
                ),
                echo=True,
            )
            output.info(
                (
                    "OneDrive batch window: "
                    f"{settings.onedrive_batch_window_seconds}s"
                ),
                echo=True,
            )
        else:
            output.info(
                (
                    "Storage: google_drive "
                    f"(folder_id={settings.google_drive_folder_id or 'root'})"
                ),
                echo=True,
            )
            output.info(
                (
                    "Google Drive batch window: "
                    f"{settings.google_drive_batch_window_seconds}s"
                ),
                echo=True,
            )
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
