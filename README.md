[![PyPI - Version](https://img.shields.io/pypi/v/telejournal.svg)](https://pypi.org/project/telejournal)
[![GitHub Tag](https://img.shields.io/github/v/tag/hugobatista/telejournal?logo=github&label=latest)](https://go.hugobatista.com/gh/telejournal/releases)
[![GHCR Tag](https://img.shields.io/github/v/tag/hugobatista/telejournal?logo=docker&logoColor=white&label=GHCR)](https://go.hugobatista.com/gh/telejournal/packages)
[![Test](https://go.hugobatista.com/gh/telejournal/actions/workflows/test.yml/badge.svg)](https://go.hugobatista.com/gh/telejournal/actions/workflows/test.yml)
[![Lint](https://go.hugobatista.com/gh/telejournal/actions/workflows/lint.yml/badge.svg)](https://go.hugobatista.com/gh/telejournal/actions/workflows/lint.yml)
# Telegram Journal Bot

A Telegram bot that journals every private message into your Obsidian daily notes.

## Features

- Private chat journal capture for text, photos, voice recordings, video messages (including circular video notes), and locations
- UTC daily note partitioning at `YYYY/YYYY-MM-DD.md`
- Media storage (photos, voice, video) in `YYYY/attachments/`
- YAML frontmatter management for `mood`, `tags`, and `created`
- In-memory state only (`context.chat_data` and `context.bot_data`)
- Date override commands (`/setdate`, `/resetdate`)
- Tags and mood management with inline keyboard callbacks

## Environment

Create a `.env` file:

```env
TELEGRAM_TOKEN=your_bot_token
VAULT_ROOT=/path/to/obsidian/vault
LOG_LEVEL=INFO
TELEGRAM_ALLOWED_USER_IDS=123456,987654
```

### Optional Environment Variables

- `MESSAGE_TIMESTAMP_WINDOW_SECONDS` (default: `60`) - Messages within this window share the same timestamp
- `SECURE_FILE_PERMISSIONS` (default: `true`) - Set restrictive permissions (0o700/0o600) on vault directories and files for security. Set to `false` only if you need broader file access.

## Run

```bash
uv sync --extra dev
uv run telegram-bot
```

Note: If you use linux secret service, namely `secret-tool`, you can skip the `.env` file step and use [secret-tool-run](https://go.hugobatista.com/gh/secret-tool-run) to automatically load secrets from your vault.

```bash
# Run with secrets from vault
secret-tool-run uv run telegram-bot
```

## Test

```bash
uv run pytest

# With full coverage and type checking
bash validate.sh
```

