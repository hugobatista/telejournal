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

## Docker

You can run the bot in Docker using either `docker run` or `docker compose`.

### Using Docker Compose

1. Create a `.env.docker` file with your bot token and settings:

    ```env
    TELEGRAM_TOKEN=your_bot_token
    VAULT_ROOT=/data
    LOG_LEVEL=INFO
    TELEGRAM_ALLOWED_USER_IDS=123456,987654
    SECURE_FILE_PERMISSIONS=false # This will avoid permission issues when running as non-root, but use with caution! 
    ```

2. Create an `obsidian-journal` directory in the same location as your `docker-compose.yml` to serve as your vault, and set permissions so the container can write to it:

    ```bash
    mkdir obsidian-journal
    chmod 777 obsidian-journal # Use with caution, or set specific user/group permissions as needed
    ```

2. Start the container:

    ```bash
    docker compose up --build
    ```

This will mount your Obsidian vault from `./obsidian-journal` to `/data` inside the container.

On SELinux-enabled Linux distributions (for example Fedora/RHEL), make sure
the bind mount uses `:Z` in `docker-compose.yml`:

```yaml
volumes:
    - ./obsidian-journal:/data:Z
```

### Using Docker Run

1. Build the image:

    ```bash
    docker build -t telejournal:latest .
    ```

2. Run the container:

    ```bash
    docker run -d \
      --env-file .env.docker \
            -v "$PWD"/obsidian-journal:/data:Z \
      --name telejournal \
      telejournal:latest
    ```

This will start the bot in detached mode, using your local `.env.docker` file and mounting your Obsidian vault.

> **Note:** If you see `pull access denied for telejournal`, you must build the image first:
>
> ```bash
> docker build -t telejournal:latest .
> ```
> Then run the container as shown above.

For troubleshooting, check logs with:

```bash
docker logs telejournal
```

