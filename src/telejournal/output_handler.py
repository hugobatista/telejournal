"""Unified output handler for logging and CLI console output."""

from __future__ import annotations

import typer

from telejournal.logger import get_logger


class OutputHandler:
    """Emit messages to both logger and console based on caller intent."""

    def __init__(self) -> None:
        """Initialize the output handler with the shared logger."""
        self.logger = get_logger()

    def _emit(self, level: str, message: str, echo: bool) -> None:
        """Log a message at ``level`` and optionally echo it to console."""
        getattr(self.logger, level)(message)
        if echo:
            typer.echo(message)

    def info(self, message: str, echo: bool = False) -> None:
        """Log an info message and optionally print it to the console."""
        self._emit("info", message, echo)

    def debug(self, message: str, echo: bool = False) -> None:
        """Log a debug message and optionally print it to the console."""
        self._emit("debug", message, echo)

    def warning(self, message: str, echo: bool = False) -> None:
        """Log a warning message and optionally print it to the console."""
        self._emit("warning", message, echo)

    def error(self, message: str, echo: bool = False) -> None:
        """Log an error message and optionally print it to the console."""
        self._emit("error", message, echo)
