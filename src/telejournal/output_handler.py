"""Unified output handler for logging and CLI console output."""

from __future__ import annotations

import typer

from telejournal.logger import get_logger


class OutputHandler:
    """Emit messages to both logger and console based on caller intent."""

    def __init__(self) -> None:
        """Initialize the output handler with the shared logger."""
        self.logger = get_logger()

    def info(self, message: str, echo: bool = False) -> None:
        """Log an info message and optionally print it to the console."""
        self.logger.info(message)
        if echo:
            typer.echo(message)

    def debug(self, message: str, echo: bool = False) -> None:
        """Log a debug message and optionally print it to the console."""
        self.logger.debug(message)
        if echo:
            typer.echo(message)

    def warning(self, message: str, echo: bool = False) -> None:
        """Log a warning message and optionally print it to the console."""
        self.logger.warning(message)
        if echo:
            typer.echo(message)

    def error(self, message: str, echo: bool = False) -> None:
        """Log an error message and optionally print it to the console."""
        self.logger.error(message)
        if echo:
            typer.echo(message)