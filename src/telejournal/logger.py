"""Logging configuration helpers for Telejournal."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
import sys

_LOG_FORMAT = "[%(asctime)s] %(levelname)s [%(name)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_SYSLOG_FORMAT = "telejournal[%(process)d]: %(message)s"
_LOGGER_NAME = "telejournal"
_SYSLOG_SOCKET_PATH = Path("/dev/log")


def _add_console_handler(logger: logging.Logger) -> None:
    """Add a stdout console handler using the standard format."""
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
    )
    logger.addHandler(console_handler)


def _is_syslog_socket_available() -> bool:
    """Return True when the platform syslog unix socket is available."""
    try:
        return _SYSLOG_SOCKET_PATH.exists() and _SYSLOG_SOCKET_PATH.is_socket()
    except OSError:
        return False


def _add_syslog_handler(logger: logging.Logger, silent: bool = True) -> bool:
    """Add syslog handler when available on the host platform."""
    if not _is_syslog_socket_available():
        if not silent:
            print(
                (
                    "Warning: Syslog socket /dev/log is unavailable; "
                    "continuing without syslog logging."
                ),
                file=sys.stderr,
            )
        return False

    try:
        syslog_handler = logging.handlers.SysLogHandler(
            address=str(_SYSLOG_SOCKET_PATH),
            facility=logging.handlers.SysLogHandler.LOG_USER,
        )
        syslog_handler.setFormatter(logging.Formatter(_SYSLOG_FORMAT))
        logger.addHandler(syslog_handler)
        return True
    except Exception as exc:  # pragma: no cover - platform-specific
        if not silent:
            print(
                (
                    "Warning: Could not set up syslog logging: "
                    f"{exc}. This may be expected on this platform."
                ),
                file=sys.stderr,
            )
        return False


def setup_default_logging() -> logging.Logger:
    """Configure startup logging before final config has been loaded."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    if not _add_syslog_handler(logger, silent=True):
        _add_console_handler(logger)
    return logger


def setup_logging(
    log_level: str | None = None, verbose: bool = False
) -> logging.Logger:
    """Configure final logging after all settings have been resolved."""
    logger = logging.getLogger(_LOGGER_NAME)
    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)
    logger.setLevel(level)
    logger.handlers.clear()

    syslog_enabled = _add_syslog_handler(logger, silent=False)
    if verbose or not syslog_enabled:
        _add_console_handler(logger)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logger


def get_logger() -> logging.Logger:
    """Get the shared Telejournal logger instance."""
    return logging.getLogger(_LOGGER_NAME)
