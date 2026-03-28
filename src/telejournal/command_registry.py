"""Central command registry used for bot wiring and help rendering."""

from __future__ import annotations

from dataclasses import dataclass

from telejournal.config import (
    STORAGE_PROVIDER_GOOGLEDRIVE,
    STORAGE_PROVIDER_ONEDRIVE,
)


@dataclass(frozen=True)
class CommandSpec:
    """Describe one command and optional provider restrictions."""

    command: str
    callback_name: str
    help_line: str
    provider_scope: frozenset[str] = frozenset()

    def is_available_for(self, storage_provider: str) -> bool:
        """Return whether command should be exposed for a storage provider."""
        if not self.provider_scope:
            return True
        return storage_provider in self.provider_scope


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="setdate",
        callback_name="setdate_command",
        help_line="/setdate YYYY-MM-DD [HH:MM:SS]  Set target note date/time",
    ),
    CommandSpec(
        command="resetdate",
        callback_name="resetdate_command",
        help_line="/resetdate  Return to today",
    ),
    CommandSpec(
        command="tags",
        callback_name="tags_command",
        help_line="/tags  Show tag buttons",
    ),
    CommandSpec(
        command="mood",
        callback_name="mood_command",
        help_line="/mood  Open mood picker",
    ),
    CommandSpec(
        command="show",
        callback_name="show_command",
        help_line="/show  Show current effective day note",
    ),
    CommandSpec(
        command="todayinhistory",
        callback_name="todayinhistory_command",
        help_line="/todayinhistory  Show same-day notes from previous years",
    ),
    CommandSpec(
        command="delete",
        callback_name="delete_command",
        help_line="/delete  Delete last entry and show deleted content",
    ),
    CommandSpec(
        command="settings",
        callback_name="settings_command",
        help_line="/settings  Guided runtime configuration",
    ),
    CommandSpec(
        command="storageauth",
        callback_name="storageauth_command",
        help_line="/storageauth [start|complete|status]  Storage device auth workflow",
        provider_scope=frozenset(
            {STORAGE_PROVIDER_ONEDRIVE, STORAGE_PROVIDER_GOOGLEDRIVE}
        ),
    ),
    CommandSpec(
        command="help",
        callback_name="help_command",
        help_line="/help",
    ),
)


def visible_command_specs(storage_provider: str) -> tuple[CommandSpec, ...]:
    """Return command specs visible for the active storage provider."""
    return tuple(
        spec for spec in COMMAND_SPECS if spec.is_available_for(storage_provider)
    )


def visible_help_lines(storage_provider: str) -> list[str]:
    """Return ordered help lines for commands visible in current provider."""
    lines = [spec.help_line for spec in visible_command_specs(storage_provider)]
    if "/tags  Show tag buttons" in lines:
        tags_index = lines.index("/tags  Show tag buttons")
        lines.insert(tags_index + 1, "/tags work kids  Add/select one or more tags")
    if "/show  Show current effective day note" in lines:
        show_index = lines.index("/show  Show current effective day note")
        lines.insert(show_index + 1, "/show YYYY-MM-DD  Show a specific day note")
    if "/delete  Delete last entry and show deleted content" in lines:
        delete_index = lines.index(
            "/delete  Delete last entry and show deleted content"
        )
        lines.insert(delete_index + 1, "/delete day [YYYY-MM-DD]  Delete full day note")
    return lines
