# Telegram Journal Bot

Telegram bot that journals every private message into Obsidian daily notes.

## Features

- Private chat journal capture for text, photos, and locations
- UTC daily note partitioning at `YYYY/YYYY-MM-DD.md`
- Photo storage in `YYYY/attachments/`
- YAML frontmatter management for `mood`, `tags`, and `created`
- In-memory state only (`context.chat_data` and `context.bot_data`)
- Date override commands (`/setdate`, `/resetdate`)
- Tags and mood management with inline keyboard callbacks
- Mood reminder checks every 5 minutes

## Environment

Create a `.env` file:

```env
TELEGRAM_TOKEN=your_bot_token
VAULT_ROOT=/path/to/obsidian/vault
LOG_LEVEL=INFO
# Optional comma-separated Telegram user IDs
# TELEGRAM_ALLOWED_USER_IDS=123456,987654
```

## Run

```bash
uv sync --extra dev
uv run telegram-bot
```

## Test

```bash
uv run pytest

# With full coverage and type checking
bash validate.sh
```

## Quality Metrics

- **Test Coverage**: 100% line coverage (516/516 statements)
- **Type Safety**: Strict mypy with disallow_untyped_defs
- **Tests**: 53 passing unit tests covering all branches and error paths
