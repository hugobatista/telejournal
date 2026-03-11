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

## Configuration

The bot supports multiple configuration sources with a clear priority order:

### Configuration Priority (highest to lowest)

1. **CLI Arguments** - Command-line options override all other sources
2. **YAML File** - Configuration file specified via `config` argument
3. **Environment Variables** - Settings from `.env` file
4. **Defaults** - Built-in defaults for optional settings

Later sources override earlier ones. For example, if you specify `--telegram-token` on the command line, it will override the `TELEGRAM_TOKEN` environment variable.

### YAML Configuration

You can provide a `config.yaml` file for more organized configuration management. The bot automatically looks for `./config.yaml` if no config path is specified.

**Example `config.yaml`:**

```yaml
telegram_token: "${TELEGRAM_TOKEN}"  # Supports environment variable expansion
vault_root: /path/to/obsidian/vault
allowed_user_ids:
  - 123456
  - 987654
log_level: INFO
message_timestamp_window_seconds: 60
secure_file_permissions: true
```

**Configuration Keys:**

- `telegram_token` (required) - Your Telegram bot token
- `vault_root` (required) - Absolute path to your Obsidian vault
- `allowed_user_ids` (required) - List of Telegram user IDs that can use the bot
- `log_level` (optional, default: `INFO`) - Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `message_timestamp_window_seconds` (optional, default: `60`) - Messages within this window share the same timestamp
- `secure_file_permissions` (optional, default: `true`) - Set restrictive file permissions for security

**Environment Variable Expansion:**

YAML configuration supports `${VAR_NAME}` syntax for environment variable expansion:

```yaml
telegram_token: "${TELEGRAM_TOKEN}"
vault_root: "${VAULT_ROOT}"
```

This allows you to keep sensitive values in environment variables while using a configuration file for other settings.

## Run

### Using Environment Variables Only

```bash
uv sync --extra dev
uv run telejournal run
```

### Using YAML Configuration File

```bash
# Automatically discovers ./config.yaml if it exists
uv run telejournal run

# Explicitly specify config file
uv run telejournal run /path/to/config.yaml
```

### Using CLI Overrides

```bash
uv run telejournal run \
  --telegram-token your_token \
  --vault-root /path/to/vault \
  --allowed-user-ids 123456,987654
```

### Verbose Output

Enable verbose logging to see startup details:

```bash
uv run telejournal run --verbose
```

### Show Version

```bash
uv run telejournal version
```

### Show Help

```bash
uv run telejournal help
```

### Using secret-tool

Note: If you use linux secret service, namely `secret-tool`, you can skip the `.env` file step and use [secret-tool-run](https://go.hugobatista.com/gh/secret-tool-run) to automatically load secrets from your vault.

```bash
# Run with secrets from vault
secret-tool-run uv run telejournal run
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

## Signalbackup-Tools HTML Import Utility

A utility is provided to convert HTML exports generated by [signalbackup-tools](https://github.com/bepaald/signalbackup-tools) to Obsidian-compatible Markdown, preserving timestamps, attachments, and replies. This is useful for importing Signal chat history into your journal vault.

### Usage

```bash
python tools/signalbackup-tool-import/html_to_markdown.py <input_html_file> <output_directory>
```

- `<input_html_file>`: Path to your HTML export from signalbackup-tools (e.g., `html/self.html`)
- `<output_directory>`: Directory where year folders and markdown files will be created

Example:

```bash
python tools/signalbackup-tool-import/html_to_markdown.py html/self.html obsidian-journal
```

This will create:
- Year folders (e.g., `2022/`, `2023/`) with daily markdown files
- An `attachments/` folder in each year for media files

See the script for more details and options.

## Running as a Systemd Service

To run the Telegram Journal Bot as a background service on Linux, you can use systemd. This ensures the bot starts on boot and restarts automatically if it fails.

### Automated Service File Generation (Recommended)

The `install-service` command generates the service file automatically with sensible defaults:

```bash
telejournal install-service
```

This will:
- Create the service file at `/etc/systemd/system/telejournal.service`
- Use your current user account
- Set working directory to `~/obsidian-journal`
- Use `.env` from your home directory
- Automatically detect the `telejournal` executable path

You can customize these defaults:

```bash
# Use custom paths and user
telejournal install-service \
  --user myuser \
  --working-directory /obsidian-journal \
  --environment-file /telejournal/.env \
  --execstart "/home/myuser/.venv/bin/telejournal run"

```

After running the command, follow the on-screen instructions to enable and start the service.

### Manual Service File Creation

Alternatively, you can manually create a service file at `/etc/systemd/system/telejournal.service` with the following content (adjust paths and user as needed):

```ini
[Unit]
Description=Telegram Journal Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/obsidian-journal
EnvironmentFile=/home/youruser/.env
ExecStart=/home/youruser/.venv/bin/telejournal run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Configuration details:**

- `User` - The user account that will run the bot (should own the vault directory)
- `WorkingDirectory` - Your Obsidian vault root directory (where notes are stored)
- `EnvironmentFile` - Path to your `.env` file with required environment variables
- `ExecStart` - Full path to the `telejournal` command (installed in your virtual environment)
- `RestartSec` - Wait 5 seconds before restarting on failure

If you installed `telejournal` system-wide via pip, you can use just `telejournal run` without the full path.

### Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable telejournal.service
sudo systemctl start telejournal.service
```

Check logs with:

```bash
journalctl -u telejournal.service -f
```

This will keep the bot running in the background and restart it automatically on failure or reboot.


