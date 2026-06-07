"""Tests for application entrypoint wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.ext import CommandHandler, ConversationHandler
from typer.testing import CliRunner

from telejournal import main as main_module
from telejournal.config import Settings

RUNNER = CliRunner()


async def _doc_callback(update: object, context: object) -> None:
    """Show command usage information."""
    del update, context


async def _no_doc_callback(update: object, context: object) -> None:
    del update, context


class _FakeBuilder:
    def __init__(self, app: object) -> None:
        self._app = app
        self.last_token: str | None = None
        self.last_post_init: object | None = None

    def token(self, token: str) -> _FakeBuilder:
        self.last_token = token
        return self

    def post_init(self, callback: object) -> _FakeBuilder:
        self.last_post_init = callback
        return self

    def build(self) -> object:
        return self._app


class _FakeApplication:
    def __init__(self, has_job_queue: bool = True) -> None:
        self.job_queue = object() if has_job_queue else None
        self.bot_data: dict[str, object] = {}
        self.run_polling_called = False
        self.allowed_updates: object = None

    def run_polling(self, *, allowed_updates: object) -> None:
        self.run_polling_called = True
        self.allowed_updates = allowed_updates


class _FakeJournalBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.shutdown_called = False

    def register_handlers(self, application: object) -> None:
        del application

    def register_jobs(self, job_queue: object) -> None:
        del job_queue

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_start_bot_registers_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bot startup should build app, register handlers/jobs, and poll."""
    settings = Settings("token", tmp_path, {1})
    app = _FakeApplication(has_job_queue=True)
    builder = _FakeBuilder(app)
    original_asyncio_run = main_module.asyncio.run

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    fake_bot = _FakeJournalBot(settings)
    monkeypatch.setattr(main_module, "JournalBot", lambda _s: fake_bot)
    monkeypatch.setattr(main_module.asyncio, "run", original_asyncio_run)
    monkeypatch.setattr(
        main_module.Application,  # type: ignore[attr-defined]
        "builder",
        staticmethod(lambda: builder),
    )

    main_module._start_bot(settings.telegram_token, settings)

    assert builder.last_token == "token"
    assert builder.last_post_init is main_module.post_init
    assert app.bot_data[main_module.SETTINGS_BOT_DATA_KEY] is settings
    assert app.run_polling_called


def test_start_bot_flushes_pending_writes_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Startup wrapper should invoke bot shutdown hook after polling exits."""
    settings = Settings("token", tmp_path, {1})
    app = _FakeApplication(has_job_queue=True)
    builder = _FakeBuilder(app)
    fake_bot = _FakeJournalBot(settings)
    original_asyncio_run = main_module.asyncio.run

    def _run(coro: object) -> None:
        original_asyncio_run(coro)

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "JournalBot", lambda _s: fake_bot)
    monkeypatch.setattr(main_module.asyncio, "run", _run)
    monkeypatch.setattr(
        main_module.Application,  # type: ignore[attr-defined]
        "builder",
        staticmethod(lambda: builder),
    )

    main_module._start_bot(settings.telegram_token, settings)
    assert fake_bot.shutdown_called


def test_start_bot_flushes_pending_writes_when_polling_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Shutdown flush should still happen when polling exits with an error."""
    settings = Settings("token", tmp_path, {1})
    app = _FakeApplication(has_job_queue=True)
    builder = _FakeBuilder(app)
    fake_bot = _FakeJournalBot(settings)
    original_asyncio_run = main_module.asyncio.run

    def _raise_polling(*, allowed_updates: object) -> None:
        del allowed_updates
        raise RuntimeError("polling failed")

    app.run_polling = _raise_polling

    def _run(coro: object) -> None:
        original_asyncio_run(coro)

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "JournalBot", lambda _s: fake_bot)
    monkeypatch.setattr(main_module.asyncio, "run", _run)
    monkeypatch.setattr(
        main_module.Application,  # type: ignore[attr-defined]
        "builder",
        staticmethod(lambda: builder),
    )

    with pytest.raises(RuntimeError, match="polling failed"):
        main_module._start_bot(settings.telegram_token, settings)
    assert fake_bot.shutdown_called


def test_fallback_command_description() -> None:
    """Fallback menu descriptions should come from command names."""
    assert main_module._fallback_command_description("start") == "Start the bot"
    assert main_module._fallback_command_description("  ") == "Use this command"


def test_command_description_from_handler_prefers_docstring() -> None:
    """Description helper should prioritize callback docstrings."""
    handler = CommandHandler("help", _doc_callback)

    description = main_module._command_description_from_handler(handler, "help")

    assert description == "Show command usage information"


def test_command_description_from_handler_fallback_without_doc() -> None:
    """Description helper should fall back when callback has no docstring."""
    handler = CommandHandler("setdate", _no_doc_callback)

    description = main_module._command_description_from_handler(
        handler,
        "setdate",
    )

    assert description == "Setdate the bot"


def test_build_bot_commands_filters_and_deduplicates() -> None:
    """Builder should include only command handlers and remove duplicates."""
    help_handler = CommandHandler("help", _doc_callback)
    duplicate_help_handler = CommandHandler("help", _no_doc_callback)
    tags_handler = CommandHandler("tags", _no_doc_callback)
    fake_app = SimpleNamespace(
        handlers={
            0: [object(), help_handler],
            1: [duplicate_help_handler, tags_handler],
        }
    )

    commands = main_module._build_bot_commands(fake_app)

    assert [(item.command, item.description) for item in commands] == [
        ("help", "Show command usage information"),
        ("tags", "Tags the bot"),
    ]


def test_build_bot_commands_includes_conversation_handlers() -> None:
    """Builder should extract commands nested in conversation handlers."""
    convo = ConversationHandler(
        entry_points=[CommandHandler("setdate", _doc_callback)],
        states={1: [CommandHandler("help", _no_doc_callback)]},
        fallbacks=[CommandHandler("resetdate", _no_doc_callback)],
    )
    fake_app = SimpleNamespace(handlers={0: [convo]})

    commands = main_module._build_bot_commands(fake_app)

    assert [(item.command, item.description) for item in commands] == [
        ("setdate", "Show command usage information"),
        ("resetdate", "Resetdate the bot"),
        ("help", "Help the bot"),
    ]


@pytest.mark.asyncio
async def test_post_init_sets_menu_commands() -> None:
    """Post-init should publish command menu entries to Telegram."""

    class _FakeBot:
        def __init__(self) -> None:
            self.commands: list[object] | None = None
            self.delete_called = False

        async def set_my_commands(self, commands: list[object]) -> None:
            self.commands = commands

        async def delete_my_commands(self) -> None:
            self.delete_called = True

    fake_bot = _FakeBot()
    fake_app = SimpleNamespace(
        handlers={0: [CommandHandler("help", _doc_callback)]},
        bot=fake_bot,
        bot_data={},
    )

    await main_module.post_init(fake_app)

    assert fake_bot.commands is not None
    assert len(fake_bot.commands) == 1
    assert fake_bot.commands[0].command == "help"
    assert fake_bot.commands[0].description == "Show command usage information"
    assert not fake_bot.delete_called


@pytest.mark.asyncio
async def test_post_init_deletes_menu_when_disabled(tmp_path: Path) -> None:
    """Post-init should delete bot commands when menu behavior is disabled."""

    class _FakeBot:
        def __init__(self) -> None:
            self.commands: list[object] | None = None
            self.delete_called = False

        async def set_my_commands(self, commands: list[object]) -> None:
            self.commands = commands

        async def delete_my_commands(self) -> None:
            self.delete_called = True

    fake_bot = _FakeBot()
    disabled_settings = Settings(
        telegram_token="token",
        vault_root=tmp_path,
        allowed_user_ids={1},
        bot_menu_enabled=False,
    )
    fake_app = SimpleNamespace(
        handlers={0: [CommandHandler("help", _doc_callback)]},
        bot=fake_bot,
        bot_data={main_module.SETTINGS_BOT_DATA_KEY: disabled_settings},
    )

    await main_module.post_init(fake_app)

    assert fake_bot.delete_called
    assert fake_bot.commands is None


def test_start_bot_raises_without_job_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Startup should fail if PTB app has no job queue available."""
    settings = Settings("token", tmp_path, {1})
    app = _FakeApplication(has_job_queue=False)
    builder = _FakeBuilder(app)

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "JournalBot", _FakeJournalBot)
    monkeypatch.setattr(
        main_module.Application,  # type: ignore[attr-defined]
        "builder",
        staticmethod(lambda: builder),
    )

    with pytest.raises(RuntimeError, match="Job queue"):
        main_module._start_bot(settings.telegram_token, settings)


def test_run_command_calls_start_bot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI run should load settings and start the bot."""
    settings = Settings("token", tmp_path, {1})
    called = {"started": False}

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "setup_default_logging", lambda: None)
    monkeypatch.setattr(main_module, "setup_logging", lambda _l, verbose: None)
    monkeypatch.setattr(main_module, "load_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(
        main_module,
        "_start_bot",
        lambda _token, _settings: called.__setitem__("started", True),
    )

    result = RUNNER.invoke(main_module.app, ["run"])

    assert result.exit_code == 0
    assert called["started"]


def test_run_command_verbose_emits_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verbose mode should print startup metadata using output handler."""
    settings = Settings("token", tmp_path, {1}, log_level="DEBUG")

    class _FakeOutput:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def info(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

        def error(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

    fake_output = _FakeOutput()
    monkeypatch.setattr(main_module, "OutputHandler", lambda: fake_output)
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "setup_default_logging", lambda: None)
    monkeypatch.setattr(main_module, "setup_logging", lambda _l, verbose: None)
    monkeypatch.setattr(main_module, "load_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(main_module, "_start_bot", lambda _t, _s: None)

    result = RUNNER.invoke(main_module.app, ["run", "--verbose"])

    assert result.exit_code == 0
    assert any("starting" in message for message in fake_output.messages)


def test_run_command_verbose_emits_github_storage_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verbose mode should include github storage summary when configured."""
    settings = Settings(
        "token",
        tmp_path,
        {1},
        log_level="DEBUG",
        storage_provider="github_repo",
        github_owner="acme",
        github_repo="journal",
        github_branch="main",
        github_token="token",
        github_batch_window_seconds=120,
    )

    class _FakeOutput:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def info(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

        def error(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

    fake_output = _FakeOutput()
    monkeypatch.setattr(main_module, "OutputHandler", lambda: fake_output)
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "setup_default_logging", lambda: None)
    monkeypatch.setattr(main_module, "setup_logging", lambda _l, verbose: None)
    monkeypatch.setattr(main_module, "load_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(main_module, "_start_bot", lambda _t, _s: None)

    result = RUNNER.invoke(main_module.app, ["run", "--verbose"])

    assert result.exit_code == 0
    assert any("Storage: github_repo" in message for message in fake_output.messages)
    assert any("batch window" in message for message in fake_output.messages)


def test_run_command_verbose_emits_onedrive_storage_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verbose mode should include OneDrive storage summary when configured."""
    settings = Settings(
        "token",
        tmp_path,
        {1},
        log_level="DEBUG",
        storage_provider="onedrive",
        onedrive_tenant_id="common",
        onedrive_client_id="client-id",
        onedrive_root_path="Apps/telejournal",
        onedrive_batch_window_seconds=120,
    )

    class _FakeOutput:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def info(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

        def error(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

    fake_output = _FakeOutput()
    monkeypatch.setattr(main_module, "OutputHandler", lambda: fake_output)
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "setup_default_logging", lambda: None)
    monkeypatch.setattr(main_module, "setup_logging", lambda _l, verbose: None)
    monkeypatch.setattr(main_module, "load_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(main_module, "_start_bot", lambda _t, _s: None)

    result = RUNNER.invoke(main_module.app, ["run", "--verbose"])

    assert result.exit_code == 0
    assert any("Storage: onedrive" in message for message in fake_output.messages)
    assert any("OneDrive batch window" in message for message in fake_output.messages)


def test_run_command_verbose_emits_google_drive_storage_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verbose mode should include Google Drive storage summary when configured."""
    settings = Settings(
        "token",
        tmp_path,
        {1},
        log_level="DEBUG",
        storage_provider="google_drive",
        google_drive_client_id="client-id",
        google_drive_client_secret="client-secret",
        google_drive_folder_id="folder-id",
        google_drive_batch_window_seconds=75,
    )

    class _FakeOutput:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def info(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

        def error(self, message: str, echo: bool = False) -> None:
            del echo
            self.messages.append(message)

    fake_output = _FakeOutput()
    monkeypatch.setattr(main_module, "OutputHandler", lambda: fake_output)
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "setup_default_logging", lambda: None)
    monkeypatch.setattr(main_module, "setup_logging", lambda _l, verbose: None)
    monkeypatch.setattr(main_module, "load_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(main_module, "_start_bot", lambda _t, _s: None)

    result = RUNNER.invoke(main_module.app, ["run", "--verbose"])

    assert result.exit_code == 0
    assert any("Storage: google_drive" in message for message in fake_output.messages)
    assert any(
        "Google Drive batch window" in message for message in fake_output.messages
    )


def test_run_command_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI run should exit non-zero on configuration errors."""
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "setup_default_logging", lambda: None)
    monkeypatch.setattr(
        main_module,
        "load_settings",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )

    result = RUNNER.invoke(main_module.app, ["run"])

    assert result.exit_code == 1
    assert "Configuration error" in result.output


def test_help_command() -> None:
    """Help subcommand should render top-level app help output."""
    result = RUNNER.invoke(main_module.app, ["help"])

    assert result.exit_code == 0
    assert "Usage" in result.output


def test_version_command() -> None:
    """Version subcommand should print package version."""
    result = RUNNER.invoke(main_module.app, ["version"])

    assert result.exit_code == 0
    assert "telejournal" in result.output


def test_run_wrapper_calls_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console-script wrapper should delegate directly to main."""
    called = {"value": False}

    def _mark() -> None:
        called["value"] = True

    monkeypatch.setattr(main_module, "main", _mark)
    main_module.run()
    assert called["value"]


def test_resolve_run_config_path(tmp_path: Path) -> None:
    """Config resolver should prioritize explicit paths and fallback to default."""
    explicit = tmp_path / "my.yaml"
    assert main_module._resolve_run_config_path(explicit) == explicit


def test_resolve_run_config_path_without_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolver should return None when no explicit/default config is found."""
    monkeypatch.chdir(tmp_path)
    assert main_module._resolve_run_config_path(None) is None


def test_resolve_run_config_path_with_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolver should pick ./config.yaml when explicit path is not provided."""
    default_config = tmp_path / "config.yaml"
    default_config.write_text("telegram_token: x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main_module._resolve_run_config_path(None) == Path("config.yaml")


def test_build_cli_overrides(tmp_path: Path) -> None:
    """CLI override builder should normalize paths to strings."""
    overrides = main_module._build_cli_overrides(
        telegram_token="token",
        storage_provider="obsidian_vault",
        obsidian_vault_root=tmp_path,
        obsidian_vault_secure_file_permissions=True,
        github_owner=None,
        github_repo=None,
        github_branch=None,
        github_token=None,
        github_path_prefix=None,
        github_api_base_url=None,
        github_batch_window_seconds=None,
        onedrive_tenant_id=None,
        onedrive_client_id=None,
        onedrive_client_secret=None,
        onedrive_root_path=None,
        onedrive_api_base_url=None,
        onedrive_batch_window_seconds=None,
        onedrive_access_token=None,
        onedrive_refresh_token=None,
        onedrive_token_expires_at_utc=None,
        allowed_user_ids="1,2",
        log_level="INFO",
        message_timestamp_window_seconds=30,
        daily_brief_time_utc="09:15",
    )

    assert overrides["telegram_token"] == "token"
    assert overrides["storage"]["provider"] == "obsidian_vault"
    assert overrides["storage"]["obsidian_vault"]["root"] == str(tmp_path)
    assert overrides["allowed_user_ids"] == "1,2"
    assert overrides["daily_brief_time_utc"] == "09:15"


def test_main_calls_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Main wrapper should delegate to Typer app callable."""
    called = {"value": False}

    monkeypatch.setattr(
        main_module,
        "app",
        lambda: called.__setitem__("value", True),
    )

    main_module.main()
    assert called["value"]


def test_get_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Current-user helper should delegate to getpass.getuser."""
    monkeypatch.setattr(main_module.getpass, "getuser", lambda: "alice")
    assert main_module._get_current_user() == "alice"


def test_get_telejournal_executable_path_prefers_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executable helper should prefer telejournal from PATH when found."""
    monkeypatch.setattr(main_module.sys, "argv", ["telejournal"])
    monkeypatch.setattr(
        main_module.shutil,
        "which",
        lambda _name: "/usr/local/bin/telejournal",
    )

    assert (
        main_module._get_telejournal_executable_path() == "/usr/local/bin/telejournal"
    )


def test_get_telejournal_executable_path_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executable helper should fallback to sibling binary near Python."""
    monkeypatch.setattr(main_module.sys, "argv", [""])
    monkeypatch.setattr(main_module.sys, "executable", "/opt/venv/bin/python")

    assert main_module._get_telejournal_executable_path() == "/opt/venv/bin/telejournal"


def test_build_systemd_service_content() -> None:
    """Service content should interpolate key configuration values."""
    content = main_module._build_systemd_service_content(
        user="alice",
        working_directory=Path("/srv/journal"),
        environment_file=Path("/srv/.env"),
        execstart="/usr/local/bin/telejournal run",
    )

    assert "User=alice" in content
    assert "WorkingDirectory=/srv/journal" in content
    assert "EnvironmentFile=/srv/.env" in content
    assert "ExecStart=/usr/local/bin/telejournal run" in content


def test_load_env_from_cwd_with_explicit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env loader should pass cwd .env when callable accepts one argument."""
    called: dict[str, object] = {}

    def _fake_loader(path: Path) -> None:
        called["path"] = path

    monkeypatch.setattr(main_module, "load_dotenv", _fake_loader)
    main_module._load_env_from_cwd()

    assert isinstance(called["path"], Path)
    assert Path(called["path"]).name == ".env"


def test_load_env_from_cwd_signature_error_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env loader should fallback to zero-arg call if signature introspection fails."""
    calls = {"count": 0}

    def _zero_arg_loader() -> None:
        calls["count"] += 1

    monkeypatch.setattr(main_module, "load_dotenv", _zero_arg_loader)
    monkeypatch.setattr(
        main_module.inspect,
        "signature",
        lambda _value: (_ for _ in ()).throw(TypeError("boom")),
    )

    main_module._load_env_from_cwd()
    assert calls["count"] == 1


def test_install_service_command_writes_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Install-service should write a unit file using defaulted values."""

    class _FakeOutput:
        def info(self, message: str, echo: bool = False) -> None:
            del message, echo

        def error(self, message: str, echo: bool = False) -> None:
            del message, echo

    output_path = tmp_path / "system" / "telejournal.service"
    monkeypatch.setattr(main_module, "OutputHandler", lambda: _FakeOutput())
    monkeypatch.setattr(main_module.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(main_module, "_get_current_user", lambda: "alice")
    monkeypatch.setattr(
        main_module,
        "_get_telejournal_executable_path",
        lambda: "/usr/local/bin/telejournal",
    )

    result = RUNNER.invoke(
        main_module.app,
        ["install-service", "--output-path", str(output_path)],
    )

    assert result.exit_code == 0
    service = output_path.read_text(encoding="utf-8")
    assert "User=alice" in service
    assert f"WorkingDirectory={tmp_path / 'obsidian-journal'}" in service
    assert f"EnvironmentFile={tmp_path / '.env'}" in service
    assert "ExecStart=/usr/local/bin/telejournal run" in service


def test_install_service_command_with_explicit_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Install-service should honor explicit CLI options and expand user paths."""

    class _FakeOutput:
        def info(self, message: str, echo: bool = False) -> None:
            del message, echo

        def error(self, message: str, echo: bool = False) -> None:
            del message, echo

    output_path = tmp_path / "custom.service"
    monkeypatch.setattr(main_module, "OutputHandler", lambda: _FakeOutput())

    result = RUNNER.invoke(
        main_module.app,
        [
            "install-service",
            "--user",
            "bob",
            "--working-directory",
            "~/journal-home",
            "--environment-file",
            "~/journal-home/.env.local",
            "--execstart",
            "/bin/custom-telejournal run",
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    service = output_path.read_text(encoding="utf-8")
    assert "User=bob" in service
    assert "ExecStart=/bin/custom-telejournal run" in service
    assert "WorkingDirectory=~/journal-home" not in service
    assert "EnvironmentFile=~/journal-home/.env.local" not in service


def test_install_service_command_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Install-service should exit non-zero when writing the file fails."""
    errors: list[str] = []

    class _FakeOutput:
        def info(self, message: str, echo: bool = False) -> None:
            del message, echo

        def error(self, message: str, echo: bool = False) -> None:
            del echo
            errors.append(message)

    def _raise_write_error(
        _self: Path,
        _data: str,
        encoding: str | None = None,
    ) -> int:
        del encoding
        raise OSError("disk full")

    output_path = tmp_path / "fail" / "telejournal.service"
    monkeypatch.setattr(main_module, "OutputHandler", lambda: _FakeOutput())
    monkeypatch.setattr(main_module.Path, "write_text", _raise_write_error)

    result = RUNNER.invoke(
        main_module.app,
        ["install-service", "--output-path", str(output_path)],
    )

    assert result.exit_code == 1
    assert any("Failed to write service file" in message for message in errors)
