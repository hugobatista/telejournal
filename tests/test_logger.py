"""Tests for logger and output handler modules."""

from __future__ import annotations

import logging

import pytest

import telejournal.logger as logger_module
from telejournal.logger import get_logger, setup_default_logging, setup_logging
from telejournal.output_handler import OutputHandler


def test_setup_default_logging_returns_named_logger() -> None:
    """Default setup should initialize and return telejournal logger."""
    logger = setup_default_logging()
    assert logger.name == "telejournal"


def test_setup_default_logging_falls_back_to_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default setup should keep logs visible when syslog is unavailable."""
    monkeypatch.setattr(
        logger_module,
        "_is_syslog_socket_available",
        lambda: False,
    )

    logger = setup_default_logging()
    console_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.handlers.SysLogHandler)
    ]

    assert console_handlers


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


def test_setup_logging_skips_syslog_when_socket_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable syslog socket should fall back to console logging."""
    monkeypatch.setattr(
        logger_module,
        "_is_syslog_socket_available",
        lambda: False,
    )

    logger = setup_logging("INFO", verbose=False)
    assert not any(
        isinstance(handler, logging.handlers.SysLogHandler)
        for handler in logger.handlers
    )
    assert any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.handlers.SysLogHandler)
        for handler in logger.handlers
    )


def test_setup_logging_avoids_duplicate_console_handler_on_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verbose mode should not duplicate the console handler on fallback."""
    monkeypatch.setattr(
        logger_module,
        "_is_syslog_socket_available",
        lambda: False,
    )

    logger = setup_logging("INFO", verbose=True)
    console_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.handlers.SysLogHandler)
    ]

    assert len(console_handlers) == 1


def test_setup_logging_adds_syslog_when_socket_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Available syslog socket should allow attaching a syslog handler."""
    monkeypatch.setattr(
        logger_module,
        "_is_syslog_socket_available",
        lambda: True,
    )

    class _DummySysLogHandler(logging.Handler):
        """Minimal stand-in for SysLogHandler to avoid platform dependency."""

        LOG_USER = 1

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__()

        def emit(self, record: logging.LogRecord) -> None:
            """Consume records in tests without performing I/O."""
            del record

    monkeypatch.setattr(logging.handlers, "SysLogHandler", _DummySysLogHandler)

    logger = setup_logging("INFO", verbose=False)
    assert any(isinstance(handler, _DummySysLogHandler) for handler in logger.handlers)


def test_is_syslog_socket_available_handles_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Socket probe should fail closed when filesystem access raises OSError."""

    class _BrokenPath:
        """Mimic a path-like object whose stat probes fail."""

        def exists(self) -> bool:
            raise OSError("boom")

        def is_socket(self) -> bool:
            return True

    monkeypatch.setattr(logger_module, "_SYSLOG_SOCKET_PATH", _BrokenPath())

    assert logger_module._is_syslog_socket_available() is False


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
