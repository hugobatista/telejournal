"""Tests for logger and output handler modules."""

from __future__ import annotations

import logging

import pytest

from telejournal.logger import get_logger, setup_default_logging, setup_logging
from telejournal.output_handler import OutputHandler


def test_setup_default_logging_returns_named_logger() -> None:
    """Default setup should initialize and return telejournal logger."""
    logger = setup_default_logging()
    assert logger.name == "telejournal"


def test_setup_logging_invalid_level_defaults_to_info() -> None:
    """Invalid levels should safely fall back to INFO."""
    logger = setup_logging("NOT_A_LEVEL", verbose=False)
    assert logger.level == logging.INFO


def test_setup_logging_verbose_adds_console_handler() -> None:
    """Verbose mode should attach a stream handler for console output."""
    logger = setup_logging("DEBUG", verbose=True)
    console_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.handlers.SysLogHandler)
    ]
    assert console_handlers


def test_get_logger_returns_singleton_instance() -> None:
    """Logger accessor should return the same logger instance."""
    assert get_logger() is get_logger()


def test_output_handler_logs_and_echoes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Output handler should always log and optionally echo."""
    handler = OutputHandler()
    called = {"echo": False}

    monkeypatch.setattr("typer.echo", lambda _msg: called.__setitem__("echo", True))
    monkeypatch.setattr(handler.logger, "info", lambda _msg: None)

    handler.info("message", echo=True)
    assert called["echo"]


def test_output_handler_error_without_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """No echo should mean no console output for error messages."""
    handler = OutputHandler()
    monkeypatch.setattr(handler.logger, "error", lambda _msg: None)

    # Should not raise and should not require console patching.
    handler.error("message", echo=False)


def test_output_handler_debug_and_warning_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Debug/warning methods should support optional echo output."""
    handler = OutputHandler()
    echoed: list[str] = []

    monkeypatch.setattr("typer.echo", lambda msg: echoed.append(msg))
    monkeypatch.setattr(handler.logger, "debug", lambda _msg: None)
    monkeypatch.setattr(handler.logger, "warning", lambda _msg: None)

    handler.debug("debug message", echo=True)
    handler.warning("warning message", echo=True)

    assert "debug message" in echoed
    assert "warning message" in echoed


def test_output_handler_error_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error method should optionally print to console when echo is enabled."""
    handler = OutputHandler()
    echoed: list[str] = []

    monkeypatch.setattr("typer.echo", lambda msg: echoed.append(msg))
    monkeypatch.setattr(handler.logger, "error", lambda _msg: None)

    handler.error("error message", echo=True)
    assert echoed == ["error message"]
