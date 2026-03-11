"""Tests for application entrypoint wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from telejournal.config import Settings
from telejournal import main as main_module

RUNNER = CliRunner()


class _FakeBuilder:
    def __init__(self, app: object) -> None:
        self._app = app
        self.last_token: str | None = None

    def token(self, token: str) -> "_FakeBuilder":
        self.last_token = token
        return self

    def build(self) -> object:
        return self._app


class _FakeApplication:
    def __init__(self, has_job_queue: bool = True) -> None:
        self.job_queue = object() if has_job_queue else None
        self.run_polling_called = False
        self.allowed_updates: object = None

    def run_polling(self, *, allowed_updates: object) -> None:
        self.run_polling_called = True
        self.allowed_updates = allowed_updates


class _FakeJournalBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.handlers_registered = False
        self.jobs_registered = False

    def register_handlers(self, application: object) -> None:
        del application
        self.handlers_registered = True

    def register_jobs(self, job_queue: object) -> None:
        del job_queue
        self.jobs_registered = True


def test_start_bot_registers_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bot startup should build app, register handlers/jobs, and poll."""
    settings = Settings("token", tmp_path, {1})
    app = _FakeApplication(has_job_queue=True)
    builder = _FakeBuilder(app)

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "JournalBot", _FakeJournalBot)
    monkeypatch.setattr(
        main_module.Application,  # type: ignore[attr-defined]
        "builder",
        staticmethod(lambda: builder),
    )

    main_module._start_bot(settings.telegram_token, settings)

    assert builder.last_token == "token"
    assert app.run_polling_called


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
        "token",
        tmp_path,
        "1,2",
        "INFO",
        30,
        True,
    )

    assert overrides["telegram_token"] == "token"
    assert overrides["vault_root"] == str(tmp_path)
    assert overrides["allowed_user_ids"] == "1,2"


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
