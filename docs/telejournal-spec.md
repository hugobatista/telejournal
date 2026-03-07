<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

## Telegram Journal Bot Specification

### Overview

A Python-based Telegram bot that captures **every private message** as a journal entry, persisting to an **Obsidian vault** using **daily notes** organized by year folders. Every message (text, photos, locations) becomes a timestamped chronological entry. Supports mood tracking via emoji buttons and inline commands, with **pure in-memory state** (no database/file persistence).

### Key Features

- **Every private message → journal entry** (text, photos, locations)
- **Daily notes**: `YYYY/YYYY-MM-DD.md` + `YYYY/attachments/YYYYMMDD_HHMMSS.jpg`
- **UTC timezone** for date boundaries
- **Emoji mood tracking** (`/mood` + 4h timer prompts)
- **Commands**: `/setdate`, `/resetdate`, `/tags`, `/help`, `/mood`
- **YAML frontmatter** for mood/tags metadata
- **In-memory only** state (`context.chat_data`)
- **Raw text + parsed Markdown** (`#tags`, `**bold**`, etc.)


### Tech Stack

```toml
# pyproject.toml (managed by uv)
[project]
name = "telegram-journal-bot"
dependencies = [
    "python-telegram-bot>=21.0",
    "python-dotenv>=1.0",
    "PyYAML>=6.0",
    "aiofiles>=24.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```


### Environment Variables (.env)

```
TELEGRAM_TOKEN=your_bot_token
VAULT_ROOT=/path/to/obsidian/vault
LOG_LEVEL=INFO
```


### Vault Structure

```
VAULT_ROOT/
├── 2026/
│   ├── 2026-03-07.md
│   └── attachments/
│       ├── 20260307_183442.jpg
│       └── 20260307_184512.jpg
└── 2025/
    ├── 2025-12-31.md
    └── attachments/
```


### Daily Note Format

```markdown
---
mood: 4
tags: ["journal", "work"]
created: 2026-03-07T00:00:00Z
---

18:34 - Thinking about **project deadlines** #work #urgent
![[2026/attachments/20260307_183442.jpg]]

18:35 Location: 38.7223° N, 9.1393° W [Map](https://maps.google.com/?q=38.7223,-9.1393)

19:15 - Meeting went well 😌 #meeting
```


## Core Message Flow

```
Private Message Received:
├── Filter: PRIVATE chat only
├── Parse: text, photo, location, voice?
├── Override: check chat_data['override_date']
├── Generate: YYYYMMDD_HHMMSS timestamp
├── Build: "HH:MM - {parsed_text}"
├── Media:
│   ├── Photo → async download → "![[YYYY/attachments/TS.jpg]]"
│   └── Location → "{lat}° N, {lon}° W [Map](https://maps.google...)"
├── Write: append to YYYY/YYYY-MM-DD.md (upsert YAML)
├── Mood Check: if >4h since last entry AND no mood → prompt
└── Log: INFO level entry written
```


## Commands Implementation

### `/help`

```
📝 Journal Bot Usage

• Every message becomes a journal entry
• Photos → embedded in attachments/ folder  
• Locations → coordinates + Google Maps link
• Mood tracked via /mood (😢 😐 😌 🙂 😊)

/setdate 2026-03-07  # Override date for next entries
/resetdate           # Back to current UTC date
/tags                # Manage day tags
/mood                # Set today's mood
```


### `/setdate YYYY-MM-DD [HH:MM:SS]`

Store in `context.chat_data['override_date'] = datetime(...)`

### `/resetdate`

```python
del context.chat_data['override_date']
```


### `/tags`

1. Read current YAML tags from today's note
2. Send message: "Current: journal, work"
3. Inline buttons: `[+personal] [-work] [+family]`
4. Callback → update YAML tags array

### `/mood`

Inline keyboard: `😢 😐 😌 🙂 😊`
Callback → `frontmatter['mood'] = 1-5`

## Mood Prompt Logic

**Only triggers when:**

1. Entry exists today (UTC)
2. >4 hours since last entry timestamp
3. No mood in YAML (`mood: null`)
```python
async def check_mood(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    note_path = get_today_note_path(chat_id)
    if has_entry_today(note_path) and hours_since_last_entry() > 4 and not has_mood(note_path):
        await context.bot.send_message(chat_id, "How's your mood today?", reply_markup=mood_keyboard())
```


## Message Type Handlers

| Type | Handler | Output |
| :-- | :-- | :-- |
| Text | `MessageHandler(filters.TEXT & filters.ChatType.PRIVATE)` | `HH:MM - {entities→MD}` |
| Photo | `MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE)` | `HH:MM - {caption}\n![[YYYY/attachments/TS.jpg]]` |
| Location | `MessageHandler(filters.LOCATION & filters.ChatType.PRIVATE)` | `HH:MM Location: {lat}° N, {lon}° W [Map](...)` |
| Voice* | Skip or textify? | TBD |
| Album | Handle `media_group_id` | Multiple photos → single entry |

## File Operations

### Daily Note Path

```python
def get_note_path(vault_root: str, dt: datetime) -> Path:
    year_dir = Path(vault_root) / str(dt.year)
    year_dir.mkdir(exist_ok=True)
    return year_dir / f"{dt.strftime('%Y-%m-%d')}.md"
```


### Photo Download

```python
async def save_photo(photo: PhotoSize, note_date: datetime, ts: str, year_dir: Path):
    attachments_dir = year_dir / "attachments"
    attachments_dir.mkdir(exist_ok=True)
    file = await photo.get_file()
    await file.download_to_drive(attachments_dir / f"{ts}.jpg")
```


### YAML + Append Atomic Write

```python
def update_note(note_path: Path, entry: str, frontmatter: dict):
    content = f"""---
{yaml.dump(frontmatter, sort_keys=False)}
---

{read_existing_body(note_path) + entry}\n"""
    async with aiofiles.open(note_path, 'w') as f:
        await f.write(content)
```


## Error Handling

| Scenario | Response | Log Level |
| :-- | :-- | :-- |
| Vault unwritable | "❌ Vault write failed. Check VAULT_ROOT permissions." | ERROR |
| Invalid /setdate | "❌ Use: /setdate YYYY-MM-DD [HH:MM:SS]" | WARNING |
| Duplicate photo TS | Append counter: `TS_1.jpg` | INFO |
| YAML parse error | Reset to default template | ERROR |
| Bot restart | State lost (expected) | INFO |

**Logging**: `logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))`

## Security Model

- **Private chats only** (`filters.ChatType.PRIVATE`)
- Optional: Whitelist your Telegram user ID
- No external API calls except Telegram + Google Maps links
- Local file writes only to `VAULT_ROOT`


## Testing Plan

### Unit Tests

```
[ ] Message parsing → correct Markdown
[ ] Photo download → correct path/Timestamp  
[ ] YAML read/write → mood/tags preserved
[ ] /setdate → overrides UTC correctly
[ ] Mood timer → prompts after 4h+entry
[ ] Tags CRUD → YAML array updates
[ ] Invalid vault path → graceful error
```


### Integration Tests

```
[ ] Send text → appears in daily note
[ ] Send photo → downloads + embeds
[ ] Send location → Maps link + coords
[ ] /mood → updates YAML
[ ] Restart bot → state resets, vault intact
[ ] Permissions error → user notified
```


### Manual Tests

```
[ ] Private chat vs group (should ignore group)
[ ] Media group (album handling)
[ ] Edge timestamps (23:59 → 00:00 boundary)
[ ] Long-running (24h+) mood timer
```


## Startup Sequence

```python
async def main():
    load_dotenv()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.LOCATION, handle_journal_entry))
    app.add_handler(CommandHandler("setdate", set_date))
    app.add_handler(CommandHandler("resetdate", reset_date))
    app.add_handler(CommandHandler("tags", tags_command))
    app.add_handler(CommandHandler("mood", mood_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Jobs
    job_queue = app.job_queue
    job_queue.run_repeating(check_mood_timers, interval=300)  # 5min
    
    await app.run_polling()
```


## Deployment

```
$ uv sync          # Install deps
$ VAULT_ROOT=/path/to/vault telegram-bot  # Run
# Or systemd + .env
```

This spec contains **everything needed** for immediate implementation. Developer can copy-paste the structure, fill in async handlers, and have a working bot in ~4-6 hours.

