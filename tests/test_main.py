"""Tests for application entrypoint wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from telegram_journal_bot.config import Settings
from telegram_journal_bot import main as main_module


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


def test_main_registers_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Entrypoint should build app, register handlers/jobs, then start polling."""
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

    main_module.main()

    assert builder.last_token == "token"
    assert app.run_polling_called


def test_main_raises_without_job_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Entrypoint should fail if PTB app has no job queue available."""
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
        main_module.main()


def test_run_calls_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console-script wrapper should delegate directly to main."""
    called = {"value": False}

    def _mark() -> None:
        called["value"] = True

    monkeypatch.setattr(main_module, "main", _mark)
    main_module.run()
    assert called["value"]


def test_configure_logging() -> None:
    """Logging setup helper should be callable for arbitrary levels."""
    main_module._configure_logging("INFO")
