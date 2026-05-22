# telejournal — Agent guide

## Commands

Run all commands via `uv run` (source installs) or direct if installed via pip.

| Action | Command |
|--------|---------|
| Lint | `uv run ruff check src/ tests/` |
| Format check | `uv run black --check src/ tests/` |
| Test (100% coverage required) | `uv run pytest --cov=src/telejournal` |
| Typecheck (strict) | `uv run mypy src --strict --no-incremental` |
| Full validate | `uv run hatch run validate` or `bash validate.sh` |

Single-test: `uv run pytest tests/test_main.py -k test_name -v`

## Architecture

- **Entrypoint**: `telejournal.main:run` → Typer CLI `run_command` → `load_settings` → `_start_bot`
- **Central class**: `JournalBot` (`src/telejournal/bot.py`) — wires handlers, jobs, and delegates to service classes
- **Service classes**: `CommandHandlerService`, `CallbackRouterService`, `NoteDeliveryService`, `MediaEntryService`, `SetDateFlowService`
- **Storage**: factory pattern via `build_repository(settings)` → `VaultRepository` (immediate), `GitHubRepository`/`OneDriveRepository`/`GoogleDriveRepository` (buffered)
- **Config loading chain**: `DEFAULT_SETTINGS` → env → YAML → CLI overrides (`merge_configs` with `merge_values` skipping `None`)

## Key conventions

- `Settings` is a frozen dataclass — use `dataclasses.replace()` to modify, never direct mutation
- 100% line coverage required across `src/telejournal/` (skipping `__init__.py`)
- mypy strict on `src/` only; tests excluded
- All async code runs through `python-telegram-bot`'s event loop (no manual `asyncio.run`)
- Tests use `FakeBuilder`/`FakeApplication`/`FakeJournalBot` + monkeypatch patterns (see `tests/test_main.py`)
- Config merging: `None` values from higher-priority sources are ignored (they don't override defaults)

## Quirks

- `uv exclude-newer = "30 days"` in `pyproject.toml` — new packages may not resolve
- Logger: syslog first (`/dev/log`), console fallback; httpx set to WARNING
- YAML config supports `${VAR_NAME}` env var expansion via `os.path.expandvars`
- `resolve_config_path` preserves `/dev/fd/*` paths for file-descriptor-backed configs
- Storage providers with `WriteVisibility.BUFFERED` (github, onedrive, google_drive) queue writes and flush asynchronously; shut down with `flush_pending(reason="shutdown")`

## Commands

- `telejournal run` — start bot
- `telejournal version` — show version
- `telejournal install-service` — generate systemd unit
