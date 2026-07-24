# Telejournal SaaS Refactor Plan

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture Analysis](#2-current-architecture-analysis)
3. [Design Decisions](#3-design-decisions)
4. [New Dependencies](#4-new-dependencies)
5. [Data Model](#5-data-model)
6. [Settings Refactor](#6-settings-refactor)
7. [UserStore & SQLAlchemy Models](#7-userstore--sqlalchemy-models)
8. [Token Encryption](#8-token-encryption)
9. [Unified Auth Layer](#9-unified-auth-layer)
10. [Per-User Repository Resolution](#10-per-user-repository-resolution)
11. [SQLite Write Buffer Provider](#11-sqlite-write-buffer-provider)
12. [Job Management at Scale](#12-job-management-at-scale)
13. [Registration & Onboarding Flow](#13-registration--onboarding-flow)
14. [Per-User Runtime Config](#14-per-user-runtime-config)
15. [Startup Wiring](#15-startup-wiring)
16. [Dockerfile & Deployment](#16-dockerfile--deployment)
17. [Test Strategy](#17-test-strategy)
18. [Implementation Order](#18-implementation-order)
19. [Known Limitations](#19-known-limitations)
20. [SaaS Anti-Pattern Findings & Mitigations](#20-saas-anti-pattern-findings--mitigations)

---

## 1. Executive Summary

Transform telejournal from a single-user (whitelist-based) personal journal bot into a multi-user platform with self-registration. The architecture is **unified** — one code path for both self-hosted and SaaS, differing only in whether self-registration is enabled.

### Key Principles

- **One architecture, one code path.** No mode flags. Self-hosted and SaaS use the same infrastructure.
- **Per-user isolation.** Each user has their own storage provider instance and settings.
- **Backward compatible.** Existing single-user self-hosted deployments work unchanged. The `UserStore` is layered on top.
- **Scalable to thousands.** Job management uses DB-driven polling, not per-user scheduler jobs.
- **Secure by default.** OAuth tokens encrypted at rest. Write buffer durable in SQLite.

### What Changes vs. What Stays

| Stays the Same | Changes |
|----------------|---------|
| Telegram bot token (one per instance) | User registry → SQLite via SQLAlchemy |
| Storage providers (GitHub, OneDrive, Google Drive) | Per-user repository resolution |
| Note format (Markdown + YAML frontmatter) | Auth layer → DB-backed |
| Formatting, logic, helpers | Daily brief / mood timers → DB polling |
| CLI (Typer) | Runtime config → per-user in DB |
| Docker build process | Write buffer → SQLite-backed |
| Test patterns (FakeBuilder, monkeypatch) | Settings → simplified, per-user defaults |

---

## 2. Current Architecture Analysis

### Settings Usage Audit

Every field in `Settings` classified by actual usage:

| Field | Category | Multi-User | Notes |
|-------|----------|-----------|-------|
| `telegram_token` | bootstrap-only | N/A | Used once at `main.py:199` to build Application |
| `log_level` | bootstrap-only | N/A | Used once at `main.py:514` to configure logging |
| `bot_menu_enabled` | bootstrap-only | N/A | Checked once at `main.py:191` in `post_init` |
| `config_path` | bootstrap-only | N/A | Used at startup for YAML loading |
| `allowed_user_ids` | runtime-global | **Must change** | Checked at `bot.py:354` on every message; iterated at `bot.py:1269,1308` |
| `message_timestamp_window_seconds` | runtime-global | **Must become per-user** | Read at `bot.py:811` on every message |
| `daily_brief_time_utc` | runtime-global | **Must become per-user** | Single schedule at `bot.py:1396-1400` |
| `tag_choices` | runtime-global | **Must become per-user** | Read at `bot_commands.py:290`, `bot_callbacks.py:421,441` |
| `prompt_for_mood_if_missing` | runtime-global | **Must become per-user** | Checked at `bot.py:970,1204` |
| `storage_provider` | factory-input | **Must become per-user** | Used at `storage/factory.py:23` and `bot.py:1364` |
| `vault_root` | factory-input | Per-user | Used at `storage/factory.py:25` |
| `github_*` (7 fields) | factory-input | Per-user | Used at `storage/factory.py:30-42` |
| `onedrive_*` (9 fields) | factory-input | Per-user | Used at `storage/factory.py:46-59` |
| `google_drive_*` (7 fields) | factory-input | Per-user | Used at `storage/factory.py:64-75` |
| `secure_file_permissions` | factory-input | Per-user | Used at `storage/factory.py:26` |

### Key Bottlenecks Identified

1. **`active_chats` is in-memory** (`bot.py:359`) — lost on restart, breaks daily briefs and mood timers
2. **Single repository per instance** (`bot.py:109`) — all users share one storage backend
3. **`allowed_user_ids` is a config set** — can't self-register, can't have per-user storage
4. **In-memory write buffer** (`github.py:62-63`, `onedrive.py:75-76`, `google_drive.py:76-77`) — SIGKILL loses writes
5. **`urllib.request` blocking HTTP** — 40-thread pool cap per provider (32 call sites across 3 providers)
6. **OAuth tokens in plaintext** (`runtime_config.py:88-117`, `onedrive.py:175`, `google_drive.py:167`)

---

## 3. Design Decisions

### 3.1 Unified Architecture (No Mode Flag)

The only difference between self-hosted and SaaS is `self_registration: bool`.

| | Self-hosted (`self_registration=false`) | SaaS (`self_registration=true`) |
|---|---|---|
| User registry | SQLite (same) | SQLite (same) |
| Per-user storage | Each member configures via `/setup` | Each user configures via `/start` + `/setup` |
| How users enter | Admin sets `allowed_user_ids` → auto-synced to DB on startup | Anyone DMs `/start` |
| `/start` command | Not registered | Registered |
| Family support | Yes — admin adds family member IDs, each member runs `/setup` | N/A |
| Obsidian vault | Available (local deployment) | Not available |

### 3.2 Storage Config Defaults in Settings

Storage fields remain in `Settings` for backward compatibility. They serve as the **bootstrap template** — when the bot starts in self-hosted mode, `allowed_user_ids` are synced to the DB and each user is created with the storage config from Settings. Once a user runs `/setup`, their DB record takes over.

In SaaS mode, storage fields in Settings are ignored — each user configures their own.

### 3.3 Per-User Settings in DB

All per-user preferences live in the `users.settings` JSON column. No more YAML-based runtime config persistence. The `persist_runtime_settings()` function and its YAML write path become standalone-only (and even then, only for system-level settings).

### 3.4 SQLAlchemy 2.0 (Not Raw SQL)

Given the schema complexity (JSON field queries for daily brief polling, write buffer table, user registry), SQLAlchemy 2.0 with async support earns its keep:
- Typed model classes with `Mapped[]` annotations
- Native JSON field queries (`User.settings["daily_brief_time_utc"]`)
- `AsyncSession` fits naturally with `aiosqlite`
- `metadata.create_all()` for Phase 1 (Alembic deferred to Phase 2)
- Test-friendly with in-memory SQLite

### 3.5 Token Encryption (Mandatory)

Fernet symmetric encryption via `cryptography`. Required in SaaS mode, optional in self-hosted. Encryption/decryption happens in the SQLAlchemy model layer — storage providers receive already-decrypted tokens.

### 3.6 SQLite Write Buffer (Default in SaaS)

In SaaS mode, a `SqliteWriteBuffer` decorator wraps each user's repository. Write operations are durable in SQLite before acknowledging to the user; reads pass through to the provider immediately. A background flush loop replays queued writes to the real provider.

In self-hosted mode, providers keep their existing in-memory queuing (fast, no DB overhead, SIGKILL risk acceptable). No changes to existing storage code.

### 3.7 DB-Driven Job Polling (Not Per-User Jobs)

Two polling jobs replace the current fan-out pattern:
- **Daily brief:** One `run_repeating(60s)` job queries DB for users whose `daily_brief_time_utc` matches the current minute
- **Mood timer:** One `run_repeating(300s)` job queries DB for users with `prompt_for_mood_if_missing=true`

Total registered jobs: **3** (daily-brief-poll, mood-timer-poll, flush-notify). Constant regardless of user count.

---

## 4. New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `sqlalchemy[asyncio]` | `>=2.0` | ORM, async sessions, typed models |
| `aiosqlite` | `>=0.20.0` | Async SQLite dialect for SQLAlchemy |
| `cryptography` | `>=42.0` | Fernet token encryption |
| `cachetools` | `>=5.0` | TTL cache for per-user settings |

Added to `pyproject.toml`:
```toml
dependencies = [
    "python-telegram-bot[job-queue]>=21.0",
    "python-dotenv>=1.0",
    "typer>=0.12",
    "PyYAML>=6.0",
    "aiofiles>=24.0",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20.0",
    "cryptography>=42.0",
    "cachetools>=5.0",
]
```

---

## 5. Data Model

### 5.1 `users` Table

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `telegram_user_id` | `INTEGER PRIMARY KEY` | — | Telegram user ID |
| `registered_at` | `TEXT NOT NULL` | `datetime('now')` | ISO-8601 registration timestamp |
| `storage_provider` | `TEXT NOT NULL` | `'obsidian_vault'` | Provider key: `obsidian_vault`, `github_repo`, `onedrive`, `google_drive` |
| `storage_config` | `JSON NOT NULL` | `{}` | Provider-specific fields (encrypted tokens) |
| `settings` | `JSON NOT NULL` | `{}` | Per-user preference overrides (source of truth) |
| `onboarding_complete` | `INTEGER NOT NULL` | `0` | Whether user has finished storage setup |
| `last_brief_sent_at` | `TEXT` | `NULL` | ISO-8601 timestamp of last daily brief sent |
| `daily_brief_time_utc` | `TEXT NOT NULL` | `'09:00'` | **Denormalized** from `settings` — `HH:MM` for indexed polling |
| `prompt_for_mood` | `INTEGER NOT NULL` | `1` | **Denormalized** from `settings` — boolean, indexed for mood polling |

**Why denormalized?** These two fields are filtered in SQL on timer intervals (60s / 300s). JSON subqueries force full table scans. Real columns + partial indexes make these O(1) lookups. The `settings` JSON remains the source of truth; the columns are kept in sync by `update_single_setting`.

### 5.2 `pending_writes` Table

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | — | Row ID |
| `user_id` | `INTEGER NOT NULL` | — | FK → `users.telegram_user_id` |
| `operation` | `TEXT NOT NULL` | — | `'put'` or `'delete'` |
| `path` | `TEXT NOT NULL` | — | Relative file path (e.g., `2026/2026-07-24.md`) |
| `payload` | `BLOB` | `NULL` | File content (NULL for deletes) |
| `status` | `TEXT NOT NULL` | `'pending'` | `'pending'`, `'processing'`, `'failed'` |
| `attempts` | `INTEGER NOT NULL` | `0` | Number of flush attempts |
| `created_at` | `TEXT NOT NULL` | `datetime('now')` | ISO-8601 creation timestamp |
| `last_error` | `TEXT` | `NULL` | Last error message on failure |

### 5.3 Per-User Settings Schema (JSON in `users.settings`)

```json
{
    "message_timestamp_window_seconds": 60,
    "daily_brief_time_utc": "09:00",
    "tag_choices": ["family", "health", "love", "hobby", "other", "finance", "social"],
    "prompt_for_mood_if_missing": true,
    "last_mood_prompt_at": "2026-07-24T09:00:00+00:00",
    "last_mood_prompt_note": "2026-07-24"
}
```

> **Denormalization:** `daily_brief_time_utc` and `prompt_for_mood_if_missing` are also stored as real columns on the `users` table (Section 5.1). The JSON is the source of truth; the columns are kept in sync by `update_single_setting`. This avoids JSON subqueries on the hot-polling paths.

Defaults applied when a key is absent:

```python
DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "message_timestamp_window_seconds": 60,
    "daily_brief_time_utc": "09:00",
    "tag_choices": ["family", "health", "love", "hobby", "other", "finance", "social"],
    "prompt_for_mood_if_missing": True,
    "last_mood_prompt_at": None,
    "last_mood_prompt_note": None,
}
```

### 5.4 Per-User Storage Config Schema (JSON in `users.storage_config`)

Provider-specific fields. Tokens are encrypted at rest.

**GitHub:**
```json
{
    "owner": "user",
    "repo": "my-journal",
    "token": "<encrypted GitHub PAT>",
    "branch": "main",
    "path_prefix": "",
    "api_base_url": "https://api.github.com",
    "batch_window_seconds": 60
}
```

**OneDrive:**
```json
{
    "tenant_id": "common",
    "client_id": "abc123",
    "client_secret": "<encrypted>",
    "root_path": "Apps/telejournal",
    "api_base_url": "https://graph.microsoft.com/v1.0",
    "batch_window_seconds": 60,
    "access_token": "<encrypted>",
    "refresh_token": "<encrypted>",
    "token_expires_at_utc": "2026-07-24T10:00:00Z"
}
```

**Google Drive:**
```json
{
    "client_id": "abc123.apps.googleusercontent.com",
    "client_secret": "<encrypted>",
    "folder_id": "1abc...",
    "batch_window_seconds": 60,
    "access_token": "<encrypted>",
    "refresh_token": "<encrypted>",
    "token_expires_at_utc": "2026-07-24T10:00:00Z"
}
```

---

## 6. Settings Refactor

### 6.1 New `Settings` Dataclass

```python
@dataclass(frozen=True)
class Settings:
    # ── System (6 fields) ──────────────────────────────────────────
    telegram_token: str
    self_registration: bool = False
    database_url: str = "sqlite+aiosqlite:///telejournal.db"
    log_level: str = "INFO"
    config_path: Path | None = None
    bot_menu_enabled: bool = True

    # ── Bootstrap (used to seed initial users in self-hosted mode) ─
    allowed_user_ids: set[int] = field(default_factory=set)

    # ── Default storage bootstrap template (will be deprecated) ────
    # These fields exist for backward compatibility with single-user
    # self-hosted configs. On startup, they seed the initial user's
    # DB record. In SaaS mode they are ignored.
    # TODO: Remove in v3 — storage config is per-user in DB only.
    storage_provider: str = STORAGE_PROVIDER_OBSIDIAN
    vault_root: Path = Path(".")
    secure_file_permissions: bool = True
    github_owner: str | None = None
    github_repo: str | None = None
    github_branch: str = "main"
    github_token: str | None = None
    github_path_prefix: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_batch_window_seconds: int = 60
    onedrive_tenant_id: str = "common"
    onedrive_client_id: str | None = None
    onedrive_client_secret: str | None = None
    onedrive_root_path: str = "Apps/telejournal"
    onedrive_api_base_url: str = "https://graph.microsoft.com/v1.0"
    onedrive_batch_window_seconds: int = 60
    onedrive_access_token: str | None = None
    onedrive_refresh_token: str | None = None
    onedrive_token_expires_at_utc: str | None = None
    google_drive_client_id: str | None = None
    google_drive_client_secret: str | None = None
    google_drive_folder_id: str | None = None
    google_drive_batch_window_seconds: int = 60
    google_drive_access_token: str | None = None
    google_drive_refresh_token: str | None = None
    google_drive_token_expires_at_utc: str | None = None
```

### 6.2 Removed from Settings (Moved to Per-User DB)

| Old Field | New Location |
|-----------|-------------|
| `message_timestamp_window_seconds` | `users.settings["message_timestamp_window_seconds"]` |
| `daily_brief_time_utc` | `users.settings["daily_brief_time_utc"]` |
| `tag_choices` | `users.settings["tag_choices"]` |
| `prompt_for_mood_if_missing` | `users.settings["prompt_for_mood_if_missing"]` |
| `storage` (StorageSettings sub-dataclass) | `users.storage_config` JSON |

### 6.3 New Settings Fields

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `self_registration` | `bool` | `False` | `TELEJOURNAL_SELF_REGISTRATION` |
| `database_url` | `str` | `"sqlite+aiosqlite:///telejournal.db"` | `TELEJOURNAL_DATABASE_URL` |

### 6.4 Config Resolution Changes

**`src/telejournal/config/resolver.py`:**
- In SaaS mode (`self_registration=true`): skip `allowed_user_ids` validation, skip storage provider validation
- In self-hosted mode: keep current validation exactly

**`src/telejournal/config_loader.py`:**
- Add env var mapping for `TELEJOURNAL_SELF_REGISTRATION` → `self_registration`
- Add env var mapping for `TELEJOURNAL_DATABASE_URL` → `database_url`

---

## 7. UserStore & SQLAlchemy Models

### 7.1 New File: `src/telejournal/models.py`

```python
"""SQLAlchemy ORM models."""

from __future__ import annotations

from sqlalchemy import String, Integer, Boolean, Text, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Declarative base for all models."""
    pass


class User(Base):
    """Telegram user registry with per-user settings and storage config."""

    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(primary_key=True)
    registered_at: Mapped[str] = mapped_column(String(32))
    storage_provider: Mapped[str] = mapped_column(String(32), default="obsidian_vault")
    storage_config: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_brief_sent_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Denormalized from settings — indexed for polling queries
    daily_brief_time_utc: Mapped[str] = mapped_column(String(5), default="09:00")
    prompt_for_mood: Mapped[bool] = mapped_column(Boolean, default=True)


class PendingWrite(Base):
    """Durable write queue entry for the SQLite write buffer."""

    __tablename__ = "pending_writes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_user_id"), index=True)
    operation: Mapped[str] = mapped_column(String(16))  # 'put' | 'delete'
    path: Mapped[str] = mapped_column(String(512))
    payload: Mapped[bytes | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_pending_writes_status_created", "status", "created_at"),
    )
```

### 7.2 New File: `src/telejournal/user_store.py`

```python
"""User registry backed by SQLAlchemy async sessions."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from telejournal.crypto import TokenEncryptor
from telejournal.models import Base, User
from cachetools import TTLCache

LOGGER = logging.getLogger(__name__)

# Default per-user settings (applied when key is absent from DB)
DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "message_timestamp_window_seconds": 60,
    "daily_brief_time_utc": "09:00",
    "tag_choices": ["family", "health", "love", "hobby", "other", "finance", "social"],
    "prompt_for_mood_if_missing": True,
    "last_mood_prompt_at": None,
    "last_mood_prompt_note": None,
}

# Fields in storage_config that must be encrypted
ENCRYPTED_STORAGE_FIELDS: dict[str, list[str]] = {
    "github_repo": ["token"],
    "onedrive": ["client_secret", "access_token", "refresh_token"],
    "google_drive": ["client_secret", "access_token", "refresh_token"],
}

# Settings that are also stored as real columns on the users table.
# The JSON is the source of truth; these columns are kept in sync
# by update_single_setting to avoid JSON subqueries on polling paths.
DENORMALIZED_SETTINGS: dict[str, str] = {
    "daily_brief_time_utc": "daily_brief_time_utc",
    "prompt_for_mood_if_missing": "prompt_for_mood",
}


class UserStore:
    """Async user registry with SQLAlchemy."""

    def __init__(
        self,
        database_url: str,
        encryptor: TokenEncryptor | None = None,
    ) -> None:
        self._engine = create_async_engine(database_url)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )
        self._encryptor = encryptor
        self._settings_cache: TTLCache[int, dict[str, Any]] = TTLCache(
            maxsize=4096, ttl=300  # 5-minute TTL (see Section 14.5)
        )

    async def initialize(self) -> None:
        """Create all tables if they don't exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose of the engine connection pool."""
        await self._engine.dispose()

    # ── User CRUD ─────────────────────────────────────────────────

    async def get_user(self, telegram_user_id: int) -> User | None:
        """Fetch a user by Telegram ID. Returns None if not found."""
        async with self._session_factory() as session:
            stmt = select(User).where(User.telegram_user_id == telegram_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user is not None:
                # Detach from session so it can be used outside the context
                session.expunge(user)
            return user

    async def create_user(self, telegram_user_id: int) -> User:
        """Create a new user with defaults. Raises if already exists."""
        now = datetime.now(UTC).isoformat()
        user = User(
            telegram_user_id=telegram_user_id,
            registered_at=now,
            settings=dict(DEFAULT_USER_SETTINGS),
            daily_brief_time_utc=DEFAULT_USER_SETTINGS["daily_brief_time_utc"],
            prompt_for_mood=DEFAULT_USER_SETTINGS["prompt_for_mood_if_missing"],
        )
        async with self._session_factory() as session:
            session.add(user)
            await session.commit()
            session.expunge(user)
        return user

    async def user_exists(self, telegram_user_id: int) -> bool:
        """Check if a user exists."""
        async with self._session_factory() as session:
            stmt = select(func.count()).select_from(User).where(
                User.telegram_user_id == telegram_user_id
            )
            result = await session.execute(stmt)
            return result.scalar() > 0

    # ── Storage Config ────────────────────────────────────────────

    async def update_storage(
        self,
        telegram_user_id: int,
        provider: str,
        config: dict[str, Any],
    ) -> None:
        """Update user's storage provider and config. Encrypts tokens."""
        encrypted_config = self._encrypt_storage_config(provider, config)
        async with self._session_factory() as session:
            stmt = (
                update(User)
                .where(User.telegram_user_id == telegram_user_id)
                .values(
                    storage_provider=provider,
                    storage_config=encrypted_config,
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def complete_onboarding(self, telegram_user_id: int) -> None:
        """Mark user's onboarding as complete."""
        async with self._session_factory() as session:
            stmt = (
                update(User)
                .where(User.telegram_user_id == telegram_user_id)
                .values(onboarding_complete=True)
            )
            await session.execute(stmt)
            await session.commit()
        # Caller must also invalidate JournalBot._repositories[telegram_user_id]
        # and JournalBot._users[telegram_user_id] after this call.

    # ── Per-User Settings ─────────────────────────────────────────

    async def get_settings(self, telegram_user_id: int) -> dict[str, Any]:
        """Return merged settings (defaults + user overrides).

        Uses an in-memory LRU cache (5-minute TTL). Cache is
        invalidated on write (see ``update_single_setting``).
        """
        if telegram_user_id in self._settings_cache:
            return self._settings_cache[telegram_user_id]

        user = await self.get_user(telegram_user_id)
        settings = dict(DEFAULT_USER_SETTINGS)
        if user is not None:
            settings.update(user.settings)
        self._settings_cache[telegram_user_id] = settings
        return settings

    async def update_settings(
        self,
        telegram_user_id: int,
        settings: dict[str, Any],
    ) -> None:
        """Replace user's settings entirely.

        Also syncs any denormalized columns from the new settings dict.
        Prefer ``update_single_setting`` for single-key updates — this
        method exists for bulk replacements (e.g., import/reset).
        """
        update_values: dict[str, Any] = {"settings": settings}

        # Sync denormalized columns from the new settings dict
        for json_key, column_name in DENORMALIZED_SETTINGS.items():
            if json_key in settings:
                update_values[column_name] = settings[json_key]

        async with self._session_factory() as session:
            stmt = (
                update(User)
                .where(User.telegram_user_id == telegram_user_id)
                .values(**update_values)
            )
            await session.execute(stmt)
            await session.commit()

        self._settings_cache.pop(telegram_user_id, None)

    async def update_single_setting(
        self,
        telegram_user_id: int,
        key: str,
        value: Any,
    ) -> dict[str, Any]:
        """Update one setting key, return merged result.

        If the key is a denormalized setting (Section 5.1), the
        corresponding real column is also updated in the same transaction.
        """
        current = await self.get_settings(telegram_user_id)
        current[key] = value

        # Build the full update values
        update_values: dict[str, Any] = {"settings": current}

        # Sync denormalized columns
        column_name = DENORMALIZED_SETTINGS.get(key)
        if column_name is not None:
            # prompt_for_mood_if_missing (bool) → prompt_for_mood (bool)
            # daily_brief_time_utc (str) → daily_brief_time_utc (str)
            update_values[column_name] = value

        async with self._session_factory() as session:
            stmt = (
                update(User)
                .where(User.telegram_user_id == telegram_user_id)
                .values(**update_values)
            )
            await session.execute(stmt)
            await session.commit()

        # Invalidate settings cache
        self._settings_cache.pop(telegram_user_id, None)

        return current

    # ── Bulk Queries ──────────────────────────────────────────────

    async def list_active_users(self) -> list[User]:
        """Return all users with onboarding_complete=True."""
        async with self._session_factory() as session:
            stmt = select(User).where(User.onboarding_complete == True)  # noqa: E712
            result = await session.execute(stmt)
            users = list(result.scalars().all())
            for u in users:
                session.expunge(u)
            return users

    async def get_brief_eligible_users(
        self, hour: int, minute: int
    ) -> list[User]:
        """Return users whose daily_brief_time_utc matches current time
        and who haven't received a brief today.

        Uses the denormalized ``daily_brief_time_utc`` column for
        indexed lookup instead of a JSON subquery.
        """
        time_prefix = f"{hour:02d}:{minute:02d}"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        async with self._session_factory() as session:
            stmt = select(User).where(
                User.onboarding_complete == True,  # noqa: E712
                User.daily_brief_time_utc.like(f"{time_prefix}%"),
                (
                    (User.last_brief_sent_at == None)  # noqa: E711
                    | (func.date(User.last_brief_sent_at) < today)
                ),
            )
            result = await session.execute(stmt)
            users = list(result.scalars().all())
            for u in users:
                session.expunge(u)
            return users

    async def mark_brief_sent(self, telegram_user_id: int) -> None:
        """Record that a daily brief was sent to this user."""
        now = datetime.now(UTC).isoformat()
        async with self._session_factory() as session:
            stmt = (
                update(User)
                .where(User.telegram_user_id == telegram_user_id)
                .values(last_brief_sent_at=now)
            )
            await session.execute(stmt)
            await session.commit()

    async def get_mood_check_eligible(self) -> list[User]:
        """Return users who want mood prompts.

        Uses the denormalized ``prompt_for_mood`` column for
        indexed lookup instead of a JSON subquery.
        """
        async with self._session_factory() as session:
            stmt = select(User).where(
                User.onboarding_complete == True,  # noqa: E712
                User.prompt_for_mood == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            users = list(result.scalars().all())
            for u in users:
                session.expunge(u)
            return users

    # ── Self-Hosted Sync ──────────────────────────────────────────

    async def sync_allowed_users(
        self,
        user_ids: set[int],
        default_storage: dict[str, Any],
        default_storage_provider: str,
    ) -> None:
        """Sync allowed_user_ids from config into DB.

        Creates missing users with default storage config.
        Users in the DB who are no longer in the whitelist are NOT
        removed (they may have journal entries). They are effectively
        deactivated — onboarding_complete is preserved so they can
        be re-activated by adding their ID back, but they will fail
        auth since the whitelist is checked first.
        """
        now = datetime.now(UTC).isoformat()
        for uid in user_ids:
            if not await self.user_exists(uid):
                async with self._session_factory() as session:
                    user = User(
                        telegram_user_id=uid,
                        registered_at=now,
                        settings=dict(DEFAULT_USER_SETTINGS),
                        storage_provider=default_storage_provider,
                        storage_config=self._encrypt_storage_config(
                            default_storage_provider, default_storage
                        ),
                        daily_brief_time_utc=DEFAULT_USER_SETTINGS["daily_brief_time_utc"],
                        prompt_for_mood=DEFAULT_USER_SETTINGS["prompt_for_mood_if_missing"],
                    )
                    session.add(user)
                    await session.commit()
                LOGGER.info("Auto-created user %s from config whitelist", uid)

    # ── Encryption Helpers ────────────────────────────────────────

    def _encrypt_storage_config(
        self, provider: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Encrypt sensitive fields in storage config."""
        if self._encryptor is None:
            return config
        fields = ENCRYPTED_STORAGE_FIELDS.get(provider, [])
        return self._encryptor.encrypt_dict(config, fields)

    def _decrypt_storage_config(
        self, provider: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Decrypt sensitive fields in storage config."""
        if self._encryptor is None:
            return config
        fields = ENCRYPTED_STORAGE_FIELDS.get(provider, [])
        return self._encryptor.decrypt_dict(config, fields)

    def decrypt_storage_config(
        self, provider: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Public wrapper for _decrypt_storage_config.

        Callers outside UserStore (e.g., JournalBot._get_repository)
        should use this instead of the private method.
        """
        return self._decrypt_storage_config(provider, config)
```

### 7.3 Helper: `get_user_settings_value`

```python
def get_user_setting(user_settings: dict[str, Any], key: str) -> Any:
    """Get a per-user setting with default fallback."""
    return user_settings.get(key, DEFAULT_USER_SETTINGS.get(key))
```

---

## 8. Token Encryption

### 8.1 New File: `src/telejournal/crypto.py`

```python
"""Token encryption using Fernet symmetric encryption."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def derive_fernet_key(master_key: str) -> bytes:
    """Derive a valid Fernet key from a passphrase."""
    digest = hashlib.sha256(master_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class TokenEncryptor:
    """Encrypt and decrypt sensitive values using Fernet."""

    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(derive_fernet_key(master_key))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string value. Returns a Fernet token as string."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a Fernet token back to plaintext."""
        return self._fernet.decrypt(ciphertext.encode()).decode()

    def encrypt_dict(
        self, d: dict[str, Any], fields: list[str]
    ) -> dict[str, Any]:
        """Encrypt specific fields in a dictionary."""
        result = dict(d)
        for field in fields:
            if field in result and result[field] is not None:
                result[field] = self.encrypt(str(result[field]))
        return result

    def decrypt_dict(
        self, d: dict[str, Any], fields: list[str]
    ) -> dict[str, Any]:
        """Decrypt specific fields in a dictionary."""
        result = dict(d)
        for field in fields:
            if field in result and result[field] is not None:
                try:
                    result[field] = self.decrypt(str(result[field]))
                except InvalidToken:
                    # Field may not be encrypted (backward compat)
                    pass
        return result
```

### 8.2 Configuration

- **Env var:** `TELEJOURNAL_ENCRYPTION_KEY` (required in SaaS mode)
- **Self-hosted:** Optional. If not set, tokens stored plaintext (acceptable for single-user risk profile)
- **SaaS:** Required. Startup fails if not set.

### 8.3 Fields Encrypted Per Provider

| Provider | Encrypted Fields |
|----------|-----------------|
| `github_repo` | `token` |
| `onedrive` | `client_secret`, `access_token`, `refresh_token` |
| `google_drive` | `client_secret`, `access_token`, `refresh_token` |

### 8.4 Encryption/Decryption Points

- **Write:** `UserStore.update_storage()` → `_encrypt_storage_config()` → stores encrypted JSON
- **Read:** `UserStore.get_user()` → caller decrypts via `decrypt_storage_config()` before passing to `build_repository_from_config()`
- **Providers:** Receive already-decrypted tokens. **Zero changes to storage provider code.**

### 8.5 Token Refresh Persistence (OAuth Providers)

**This is a critical gap in the current provider code that the SaaS plan must address.**

OneDrive (`onedrive.py`) and Google Drive (`google_drive.py`) auto-refresh OAuth tokens when they expire. Currently, refreshed tokens are persisted back to the YAML config file via `_persist_tokens_if_possible()` (e.g., `onedrive.py:138-175`). In SaaS mode, there is no YAML config — tokens must be saved to `users.storage_config` in SQLite.

**Approach: Inject a `token_persister` callback into the provider at construction time.**

The `SqliteWritesRepository` (Section 11.2) monitors each provider for token changes and calls `token_persister(provider_name, updated_tokens)` to save them back to the DB.

**Factory changes:**

```python
async def _token_persister(
    user_id: int,
    user_store: UserStore,
    provider_name: str,
    tokens: dict[str, Any],
) -> None:
    """Callback: persist refreshed OAuth tokens to user's storage_config."""
    user = await user_store.get_user(user_id)
    if user is None:
        return
    config = dict(user.storage_config)
    config.update(tokens)
    await user_store.update_storage(user_id, user.storage_provider, config)
```

**When building a SaaS repository:**

```python
# Build real provider with config_path=None (no YAML persistence)
config_no_yaml = {**config, "config_path": None}
real_repo = build_repository_from_config(provider_name, config_no_yaml)

# Wrap with SQLite buffer, injecting token persistence
wrapped = SqliteWritesRepository(
    provider=real_repo,
    user_id=user_id,
    session_factory=session_factory,
    token_persister=partial(
        _token_persister, user_id, user_store, provider_name
    ),
)
```

**What happens in the provider:**
- Provider's `_persist_tokens_if_possible` is called on token refresh (existing behavior, unchanged)
- The `SqliteWritesRepository` shim intercepts this: instead of writing to a YAML file, it calls the async `token_persister` callback which saves to SQLite
- If `config_path` is `None`, the provider logs a debug message and skips YAML write — the `SqliteWritesRepository` handles persistence instead

---



## 9. Unified Auth Layer

### 9.1 New Dataclass

```python
# In src/telejournal/bot.py

@dataclass(frozen=True)
class AuthResult:
    """Result of an authorization check."""
    authorized: bool
    reason: str  # "ok" | "not_registered" | "onboarding_incomplete" | "not_whitelisted" | "not_private"
    user_id: int | None = None
```

### 9.2 New Method: `_check_auth`

```python
async def _check_auth(self, update: Update) -> AuthResult:
    """Unified authorization check for both self-hosted and SaaS modes."""
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return AuthResult(authorized=False, reason="not_private")

    user = update.effective_user
    if not user:
        return AuthResult(authorized=False, reason="not_private")

    if not self._settings.self_registration:
        return self._check_auth_self_hosted(user)
    return await self._check_auth_saas(user)

def _check_auth_self_hosted(self, user) -> AuthResult:
    """Self-hosted: check config whitelist + in-memory user cache.

    Uses ``self._users`` (populated during ``sync_allowed_users``) for
    a synchronous lookup with no DB call on every message.  The cache
    is authoritative for onboarding status — if a user runs ``/setup``
    and calls ``complete_onboarding(user_id)``, the cache is
    invalidated and rebuilt on next access.
    """
    if user.id not in self._settings.allowed_user_ids:
        return AuthResult(authorized=False, reason="not_whitelisted")

    # Check onboarding via in-memory cache (sync, no DB hit)
    cached = self._users.get(user.id)
    if cached is None:
        # Cache miss (startup race) — assume onboarding incomplete
        # so the user is directed to /setup
        return AuthResult(authorized=False, reason="onboarding_incomplete", user_id=user.id)
    if not cached.onboarding_complete:
        return AuthResult(authorized=False, reason="onboarding_incomplete", user_id=user.id)

    return AuthResult(authorized=True, reason="ok", user_id=user.id)

async def _check_auth_saas(self, user) -> AuthResult:
    """SaaS: check DB for registration and onboarding status."""
    db_user = await self._user_store.get_user(user.id)
    if db_user is None:
        return AuthResult(authorized=False, reason="not_registered", user_id=user.id)
    if not db_user.onboarding_complete:
        return AuthResult(authorized=False, reason="onboarding_incomplete", user_id=user.id)
    return AuthResult(authorized=True, reason="ok", user_id=user.id)
```

**In-memory user cache (`JournalBot.__init__`):**

```python
# In JournalBot.__init__:
self._users: dict[int, User] = {}  # user_id → User (populated by sync)
```

**Cache lifecycle:**
- **Self-hosted:** `sync_allowed_users` populates `self._users` on startup. When a user runs `/setup` and onboarding completes, call `self._users[user_id] = updated_user` to invalidate.
- **SaaS:** Not used — `_check_auth_saas` queries DB directly (users register at runtime, no sync).

This avoids a DB call on every incoming message in self-hosted mode while keeping the auth check synchronous.

### 9.3 Impact on Callers (22 sites)

**Before (current):**
```python
if not self._is_private_and_authorized(update):
    return
```

**After (unified):**
```python
auth = await self._check_auth(update)
if not auth.authorized:
    return
```

### 9.4 Files Affected

| File | Lines | Change |
|------|-------|--------|
| `bot.py` | 346-354 | Replace `_is_private_and_authorized` with `_check_auth` |
| `bot.py` | 1003, 1174, 1293 | Update call sites in `handle_journal_entry`, `callback_router`, `todayinhistory_command` |
| `bot_commands.py` | 45, 68, 94, 122, 136, 202, 247, 262, 300, 316 | Constructor param + 10 handler guards |
| `bot_setdate.py` | 33, 44, 65, 96 | Constructor param + 3 handler guards |

### 9.5 Response Messages

```python
async def _handle_auth_response(self, update: Update, reason: str) -> None:
    """Send appropriate response for unauthorized access."""
    messages = {
        "not_registered": "Welcome to Telejournal! Send /start to register.",
        "onboarding_incomplete": "Let's set up your journal storage. Send /setup to continue.",
        "not_whitelisted": "",  # Silent ignore (self-hosted)
        "not_private": "",      # Silent ignore
    }
    text = messages.get(reason)
    if text and update.effective_message:
        await update.effective_message.reply_text(text)
```

---

## 10. Per-User Repository Resolution

### 10.1 New Method: `_get_repository`

```python
# In JournalBot.__init__:
self._repository: Any = build_repository(settings)  # Keep for single-user fast path
self._repositories: dict[int, Any] = {}  # user_id → repository cache

async def _get_repository(self, user_id: int) -> Any | None:
    """Return the repository for a specific user.

    Resolution order:
    1. In-memory cache (``self._repositories``) — instant, no DB
    2. Self-hosted single-user fast path — use ``self._repository``
       directly (built from Settings at startup)
    3. Build from DB via ``UserStore`` and cache

    Returns ``None`` if the user has no storage configured (SaaS
    mode, onboarding incomplete, or missing from DB).  Callers
    must check for ``None`` before using the repository.
    """
    # Fast path: single-user self-hosted, no DB lookup needed
    if (
        not self._settings.self_registration
        and len(self._settings.allowed_user_ids) == 1
        and user_id in self._settings.allowed_user_ids
        and len(self._repositories) == 0  # Cold cache on first call
    ):
        self._repositories[user_id] = self._repository
        return self._repository

    # Cached path
    if user_id in self._repositories:
        return self._repositories[user_id]

    # Build from DB
    db_user = await self._user_store.get_user(user_id)
    if db_user is None or not db_user.onboarding_complete:
        if self._settings.self_registration:
            return None  # SaaS: user not ready, caller must bail
        return self._repository  # Self-hosted fallback

    decrypted_config = self._user_store.decrypt_storage_config(
        db_user.storage_provider, db_user.storage_config
    )

    if self._settings.self_registration:
        # SaaS: wrap with SQLite audit trail
        from functools import partial
        from telejournal.write_buffer import SqliteWritesRepository

        token_persister = partial(
            _token_persister, user_id, self._user_store, db_user.storage_provider
        )
        repo = build_saas_repository(
            user_id=user_id,
            provider=db_user.storage_provider,
            config=decrypted_config,
            session_factory=self._user_store._session_factory,
            token_persister=token_persister,
        )
    else:
        # Self-hosted: build directly from config
        repo = build_repository_from_config(
            db_user.storage_provider, decrypted_config
        )

    self._repositories[user_id] = repo
    return repo
```

### 10.2 New Factory: `build_repository_from_config`

```python
# In src/telejournal/storage/factory.py

def build_repository_from_config(
    provider: str,
    config: dict[str, Any],
) -> VaultRepository | GitHubRepository | OneDriveRepository | GoogleDriveRepository:
    """Build a storage repository from a provider key and config dict."""
    if provider == STORAGE_PROVIDER_OBSIDIAN:
        return VaultRepository(
            vault_root=Path(config.get("root", ".")),
            secure_permissions=config.get("secure_file_permissions", True),
        )
    if provider == STORAGE_PROVIDER_GITHUB:
        owner = config.get("owner", "")
        repo = config.get("repo", "")
        token = config.get("token", "")
        if not all([owner, repo, token]):
            raise ValueError("GitHub storage requires owner, repo, and token")
        return GitHubRepository(
            owner=owner,
            repo=repo,
            token=token,
            branch=config.get("branch", "main"),
            path_prefix=config.get("path_prefix", ""),
            api_base_url=config.get("api_base_url", "https://api.github.com"),
            batch_window_seconds=config.get("batch_window_seconds", 60),
        )
    if provider == STORAGE_PROVIDER_ONEDRIVE:
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")
        if not client_id or not client_secret:
            raise ValueError("OneDrive storage requires client_id and client_secret")
        return OneDriveRepository(
            tenant_id=config.get("tenant_id", "common"),
            client_id=client_id,
            client_secret=client_secret,
            root_path=config.get("root_path", "Apps/telejournal"),
            api_base_url=config.get("api_base_url", "https://graph.microsoft.com/v1.0"),
            batch_window_seconds=config.get("batch_window_seconds", 60),
            access_token=config.get("access_token"),
            refresh_token=config.get("refresh_token"),
            token_expires_at_utc=config.get("token_expires_at_utc"),
        )
    if provider == STORAGE_PROVIDER_GOOGLEDRIVE:
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")
        if not client_id or not client_secret:
            raise ValueError("Google Drive storage requires client_id and client_secret")
        return GoogleDriveRepository(
            client_id=client_id,
            client_secret=client_secret,
            folder_id=config.get("folder_id"),
            batch_window_seconds=config.get("batch_window_seconds", 60),
            access_token=config.get("access_token"),
            refresh_token=config.get("refresh_token"),
            token_expires_at_utc=config.get("token_expires_at_utc"),
        )
    raise ValueError(f"Unsupported storage provider: {provider}")
```

### 10.3 Service Class Signature Changes

All four service classes change `repository_provider: Callable[[], Any]` → `repository_provider: Callable[[int], Any]`:

| File | Line | Current | New |
|------|------|---------|-----|
| `bot_delivery.py` | 34 | `Callable[[], Any]` | `Callable[[int], Any]` |
| `bot_media.py` | 29 | `Callable[[], Any]` | `Callable[[int], Any]` |
| `bot_callbacks.py` | 38 | `Callable[[], Any]` | `Callable[[int], Any]` |
| `bot_commands.py` | 32 | `Callable[[], Any]` | `Callable[[int], Any]` |

### 10.4 How user_id Flows Through Handlers

Every handler already has `update.effective_user.id`. The pattern:

```python
async def some_handler(self, update, context):
    auth = await self._check_auth(update)
    if not auth.authorized:
        return
    user_id = auth.user_id
    repository = await self._get_repository(user_id)
    # ... use repository
```

For service class methods, `user_id` is passed as a parameter:

```python
# bot_delivery.py
async def send_historical_notes_for_chat(
    self, chat_id, bot, reference_dt, render_mode, user_id
):
    repository = self._repository_provider(user_id)
    # ... rest unchanged
```

### 10.5 All `self._repository` Accesses in `bot.py`

Every direct access to `self._repository` (the old single-user pattern) must be routed through `_get_repository(user_id)`. These are the 14 sites:

| Line | Context | Change Required |
|------|---------|----------------|
| 109 | `self._repository = build_repository(settings)` | Keep as fallback; also populate `_repositories` cache |
| 115 | `repository_provider=lambda: self._repository` | → `lambda uid: await self._get_repository(uid)` (NoteDeliveryService) |
| 121 | `repository_provider=lambda: self._repository` | → `lambda uid: await self._get_repository(uid)` (MediaEntryService) |
| 132 | `repository_provider=lambda: self._repository` | → `lambda uid: await self._get_repository(uid)` (CallbackRouterService) |
| 169 | `repository_provider=lambda: self._repository` | → `lambda uid: await self._get_repository(uid)` (CommandHandlerService) |
| 376 | `self._repository.capabilities.write_visibility` | → `await self._get_repository(auth.user_id).capabilities.write_visibility` |
| 384 | `cast(SupportsFlushEventSubscription, self._repository)` | → `cast(SupportsFlushEventSubscription, await self._get_repository(auth.user_id))` |
| 758 | `await self._repository.append_entry(...)` | → `repo = await self._get_repository(auth.user_id); await repo.append_entry(...)` |
| 797 | `return await self._repository.update_marked_entry(...)` | → `repo = await self._get_repository(auth.user_id); return await repo.update_marked_entry(...)` |
| 978-979 | `await self._repository.note_has_entry(note_dt)` / `note_has_mood(note_dt)` | → `repo = await self._get_repository(auth.user_id)` |
| 1217-1218 | `await self._repository.note_has_entry(note_dt)` / `note_has_mood(note_dt)` | → `repo = await self._get_repository(user.telegram_user_id)` |
| 1256 | `self._repository,` passed to `build_authorization_instructions` | → `await self._get_repository(auth.user_id),` |
| 1405 | `getattr(self._repository, "flush_pending", None)` | → `getattr(await self._get_repository(auth.user_id), "flush_pending", None)` |

The 4 lambda wrappers (lines 115, 121, 132, 169) need special attention: the service class constructors accept `Callable[[], Any]` and the lambdas capture `self._repository` at construction time.  After the refactor, these become `Callable[[int], Any]` and the lambdas must call `await self._get_repository(uid)` — which means the service class method signatures must also accept `user_id` and the lambdas must be async.  See Section 10.3 for the service class signature changes.

---

## 11. SQLite Write Buffer Provider

### 11.1 Design: Decorator Pattern

In SaaS mode, write operations must be durable before acknowledging to the user. Rather than modifying the three cloud providers (which already work correctly for self-hosted), we apply a **Decorator pattern**: a `SqliteWritesRepository` wraps a real provider and implements the same duck-typed repository interface.

```
Self-hosted:
  service class → provider.append_entry() → provider._queue_put_content() → in-memory → _flush_loop → API

SaaS:
  service class → SqliteWritesRepository.append_entry()
                    ├── delegate real work to wrapped provider
                    ├── queue operation in SQLite pending_writes
                    └── return (acknowledge to user)
                    ...
  background task → read pending_writes → verify provider completed → DELETE
```

Key properties:
- **Reads** (`get_note_content`, `get_same_day_previous_year_notes`, etc.) pass through to the provider immediately — no buffering needed
- **Writes** (`append_entry`, `save_photo`, `update_marked_entry`, etc.) execute against the real provider **and** record the operation in SQLite. The SQLite record is a durability audit trail, not a queue
- **Flush loop** is not needed for correctness — writes succeed or fail in real time. The SQLite table serves as a **journal**: on restart, any `pending` writes represent operations that completed in the provider but weren't recorded as such
- **Token refresh** callbacks are injected into providers so refreshed tokens persist back to `users.storage_config`

This avoids rewriting the three providers entirely. No `InMemoryWriteBuffer` is needed — self-hosted mode keeps providers' existing in-memory queuing unchanged.

### 11.2 New File: `src/telejournal/write_buffer.py`

```python
"""Durable write buffer decorator for storage providers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from telejournal.models import PendingWrite

LOGGER = logging.getLogger(__name__)

FlushHandler = Callable[[int, str, bytes | None], Awaitable[bool]]


class SqliteWritesRepository:
    """Decorator around a storage provider that records writes to SQLite.

    Reads pass through to the wrapped provider immediately.
    Writes execute against the provider AND are recorded in the
    ``pending_writes`` table as an audit trail.

    **This is not a queue.** Writes succeed or fail against the real
    provider in real time. The SQLite record is a durability audit
    trail: on restart, any ``pending`` rows indicate writes that
    completed in the provider but were not acknowledged (e.g.,
    SIGKILL during write). These are logged for manual review, not
    auto-replayed, because replaying arbitrary write operations
    risks duplicates or side effects.

    This wraps a **single user's** repository. Each user gets their
    own ``SqliteWritesRepository(repo)`` instance.

    **Token refresh:** For OAuth providers (OneDrive, Google Drive),
    the constructor monkey-patches ``_persist_tokens_if_possible``
    so refreshed tokens are saved to ``users.storage_config`` in
    SQLite instead of a YAML file.  The patched method is stored as
    ``_orig_persist_tokens`` on the provider for cleanup in
    ``shutdown()``.
    """

    def __init__(
        self,
        provider: Any,
        user_id: int,
        session_factory: async_sessionmaker,
        token_persister: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._provider = provider
        self._user_id = user_id
        self._session_factory = session_factory
        self._token_persister = token_persister
        self._refresh_task: asyncio.Task | None = None

        # Inject token persistence callback into OAuth providers
        if token_persister is not None:
            self._patch_token_refresh(provider)

    def _patch_token_refresh(self, provider: Any) -> None:
        """Monkey-patch OAuth providers to persist tokens to DB.

        Requires the provider to have ``_persist_tokens_if_possible``
        (sync method that writes tokens to YAML when ``config_path``
        is set).  We replace it with a lambda that schedules an async
        task calling ``_handle_token_refresh`` instead.

        The original method is saved as ``_orig_persist_tokens`` on
        the provider so ``shutdown()`` can restore it.
        """
        if hasattr(provider, "_orig_persist_tokens"):
            return  # Already patched

        if not hasattr(provider, "_persist_tokens_if_possible"):
            return  # Not an OAuth provider

        orig = provider._persist_tokens_if_possible
        provider._orig_persist_tokens = orig  # type: ignore[attr-defined]
        provider._persist_tokens_if_possible = lambda: asyncio.create_task(  # type: ignore[attr-defined]
            self._handle_token_refresh(provider),
            name=f"token-refresh-{self._user_id}",
        )

    async def shutdown(self) -> None:
        """Cancel pending token refresh tasks and restore original methods."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    # ── Reads pass through ────────────────────────────────────────

    async def get_note_content(self, note_dt: datetime) -> str | None:
        return await self._provider.get_note_content(note_dt)

    async def get_same_day_previous_year_notes(
        self, reference_dt: datetime
    ) -> list[tuple[datetime, str]]:
        return await self._provider.get_same_day_previous_year_notes(reference_dt)

    # ... all other read methods delegate to self._provider ...

    # ── Writes execute + audit trail ──────────────────────────────

    async def append_entry(self, note_dt: datetime, entry: str, **kwargs: Any) -> Any:
        result = await self._provider.append_entry(note_dt, entry, **kwargs)
        await self._record("append_entry", path=note_dt.strftime("%Y/%Y-%m-%d.md"), payload=entry.encode())
        return result

    async def update_marked_entry(
        self, note_dt: datetime, marker: str, body: str, **kwargs: Any
    ) -> bool:
        result = await self._provider.update_marked_entry(note_dt, marker, body, **kwargs)
        await self._record("update_marked_entry", path=f"{marker}:{note_dt.isoformat()}", payload=body.encode())
        return result

    async def save_photo(self, photo: Any, note_dt: datetime, ts: str) -> str:
        result = await self._provider.save_photo(photo, note_dt, ts)
        await self._record("save_photo", path=result, payload=None)
        return result

    # ... all other write methods follow the same pattern ...

    # ── Internal ───────────────────────────────────────────────────

    async def _record(
        self,
        operation: str,
        *,
        path: str = "",
        payload: bytes | None = None,
    ) -> None:
        """Record a completed write for the audit trail.

        The ``path`` and ``payload`` fields are populated for
        diagnostic purposes.  They are NOT used for automatic
        replay — see ``replay_pending()``.

        All writes are recorded with ``status="completed"`` because
        the provider write has already succeeded by the time this is
        called.  The ``pending`` status was removed because the
        pre-write + post-write pattern could not detect SIGKILL
        reliably, and automatic replay risks duplicates.
        """
        write = PendingWrite(
            user_id=self._user_id,
            operation=operation,
            path=path,
            payload=payload,
            status="completed",
            created_at=datetime.now(UTC).isoformat(),
        )
        async with self._session_factory() as session:
            session.add(write)
            await session.commit()

    async def _handle_token_refresh(self, provider: Any) -> None:
        """Persist refreshed OAuth tokens to DB via the injected callback.

        The ``token_persister`` callable is created via ``partial`` with
        ``user_id``, ``user_store``, and ``provider_name`` already bound
        (Section 8.5), so it only needs the token payload.
        """
        if self._token_persister is None:
            return
        tokens = {}
        if hasattr(provider, "_access_token"):
            tokens["access_token"] = provider._access_token
        if hasattr(provider, "_refresh_token"):
            tokens["refresh_token"] = provider._refresh_token
        if hasattr(provider, "_token_expires_at_utc"):
            tokens["token_expires_at_utc"] = provider._token_expires_at_utc

        if tokens:
            await self._token_persister(tokens)

    async def replay_pending(self) -> int:
        """Audit any writes from a previous session.

        All writes are recorded with ``status="completed"`` (see
        ``_record``).  This method exists as a hook for future
        crash-recovery enhancements and currently returns 0.

        **Automatic replay is intentionally not implemented** because:
        1. Write operations (``append_entry``, ``save_photo``) have
           side effects that cannot be safely retried
        2. Photo payloads are not stored (only file paths)
        3. Retrying would risk duplicate entries or broken references

        For data recovery, check the provider directly (GitHub API
        history, OneDrive versioning, etc.).
        """
        return 0

    # ── Convenience delegates (support the duck-typed interface) ──

    def __getattr__(self, name: str) -> Any:
        """Fallback: delegate any unknown attribute to the wrapped provider."""
        return getattr(self._provider, name)
```

### 11.3 Deployment Modes

| Mode | Strategy | What Happens |
|------|----------|-------------|
| Self-hosted (single or family) | No buffer | Providers keep their existing in-memory `_pending_puts`/`_pending_deletes` + `_flush_loop`. No changes to `github.py`, `onedrive.py`, `google_drive.py`. |
| SaaS | `SqliteWritesRepository` decorator | Each user's repository is wrapped. Writes go to provider + SQLite journal. Reads pass through. `replay_pending()` runs on startup. |

### 11.4 Provider Changes

**Self-hosted mode: zero changes.** The providers (`GitHubRepository`, `OneDriveRepository`, `GoogleDriveRepository`) keep their internal queuing. The existing `_flush_loop`, `_queue_put_content`, `_queue_delete_content`, and `_flush_pending` methods continue working exactly as before.

**SaaS mode: minimal changes.**

1. **Token persistence injection.** The factory creates the provider, then wraps it in `SqliteWritesRepository`. The `_token_persister` callback is injected so that OAuth token refreshes are saved to `users.storage_config` in SQLite instead of the YAML config file.

2. **`config_path=None`.** In SaaS mode, providers are constructed with `config_path=None` (no YAML file). The existing `_persist_tokens_if_possible` method in each provider already handles `config_path=None` gracefully — it logs a debug message and returns.

3. **No internal queuing.** The `SqliteWritesRepository` decorator bypasses the providers' `_queue_put_content` / `_queue_delete_content` / `_flush_loop`. Writes go directly to the provider's private `_write_note` / `_delete_content` / etc. methods, and the result is journaled to SQLite. The provider's own `_flush_loop` task is never started because the `SqliteWritesRepository` doesn't call `_ensure_flush_task()`.

4. **Token refresh contract.** The `SqliteWritesRepository._patch_token_refresh` method requires the provider to have:
   - `_persist_tokens_if_possible()` — a sync method called after token refresh.  The original is saved as `_orig_persist_tokens` on the provider.
   - `_apply_token_payload()` — present on OneDrive and Google Drive providers.
   - Private token attributes: `_access_token`, `_refresh_token`, `_token_expires_at_utc`.

   If the provider lacks `_persist_tokens_if_possible`, patching is silently skipped.  The created `asyncio.Task` is stored as `_refresh_task` for cleanup in `shutdown()`.

**Factory changes:**

```python
def build_saas_repository(
    user_id: int,
    provider: str,
    config: dict[str, Any],
    session_factory: async_sessionmaker,
    token_persister: Callable | None = None,
) -> SqliteWritesRepository:
    """Build a repository wrapped with the SQLite write buffer."""
    config = {**config, "config_path": None}  # No YAML persistence
    real_repo = build_repository_from_config(provider, config)
    return SqliteWritesRepository(
        provider=real_repo,
        user_id=user_id,
        session_factory=session_factory,
        token_persister=token_persister,
    )
```

---

## 12. Job Management at Scale

### 12.1 Current Jobs (to be replaced)

| Job | Current Implementation | Problem |
|-----|----------------------|---------|
| `send_daily_brief` | `run_daily` at `Settings.daily_brief_time_utc`, fans out to all `allowed_user_ids` | Single time for all users, iterates in-memory set |
| `check_mood_timers` | `run_repeating(300s)`, iterates `active_chats` | In-memory set, lost on restart |
| `dispatch_storage_flush_notifications` | `run_repeating(1s)`, drains `asyncio.Queue` | Instance-local, acceptable |
| `send_startup_message` | `run_once(when=0)`, iterates `allowed_user_ids` | One-time, needs DB iteration |

### 12.2 New Jobs

| Job | Type | Interval | Purpose |
|-----|------|----------|---------|
| `daily-brief-poll` | `run_repeating` | 60s | Query DB for users whose brief_time matches current minute |
| `mood-timer-poll` | `run_repeating` | 300s | Query DB for users with mood prompts enabled |
| `flush-notify` | `run_repeating` | 1s | Unchanged — drains flush event queue |

**Total: 3 jobs.** Constant regardless of user count.

### 12.3 Daily Brief Polling Implementation

```python
async def _poll_daily_brief(self, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single polling job — sends briefs to users whose time matches now."""
    now = datetime.now(UTC)
    users = await self._user_store.get_brief_eligible_users(now.hour, now.minute)

    if not users:
        return

    # Process in batches to avoid blocking event loop
    batch_size = 50
    for i in range(0, len(users), batch_size):
        batch = users[i : i + batch_size]
        await asyncio.gather(
            *[
                self._safe_send_daily_brief(user, context.bot, now)
                for user in batch
            ],
            return_exceptions=True,
        )

async def _safe_send_daily_brief(
    self, user: User, bot: Any, now: datetime
) -> None:
    """Send daily brief to one user with error handling."""
    try:
        await self._send_daily_brief_for_chat(
            user.telegram_user_id, bot, now
        )
        await self._user_store.mark_brief_sent(user.telegram_user_id)
    except (OSError, TelegramError):
        LOGGER.exception(
            "Failed to send daily brief to user=%s", user.telegram_user_id
        )
```

### 12.4 Mood Timer Polling Implementation

```python
async def _poll_mood_timers(self, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check all eligible users for missing mood entries."""
    users = await self._user_store.get_mood_check_eligible()
    now = datetime.now(UTC)

    for user in users:
        try:
            await self._check_mood_for_user(user, context.bot, now)
        except (OSError, TelegramError):
            LOGGER.exception(
                "Mood check failed for user=%s", user.telegram_user_id
            )

async def _check_mood_for_user(
    self, user: User, bot: Any, now: datetime
) -> None:
    """Check one user's mood state and prompt if needed."""
    repository = await self._get_repository(user.telegram_user_id)
    note_dt = now

    note_has_entry = await repository.note_has_entry(note_dt)
    note_has_mood = await repository.note_has_mood(note_dt)

    if not note_has_entry or note_has_mood:
        return

    # Check cooldown from per-user settings
    user_settings = await self._user_store.get_settings(user.telegram_user_id)
    last_prompted_at_str = user_settings.get("last_mood_prompt_at")
    last_prompted_note = user_settings.get("last_mood_prompt_note")
    note_key = now.strftime("%Y-%m-%d")

    if last_prompted_note != note_key:
        last_prompted_at_str = None

    last_prompted_at = (
        datetime.fromisoformat(last_prompted_at_str)
        if last_prompted_at_str
        else None
    )

    if not should_prompt_for_mood(
        note_has_entry=True,
        note_has_mood=False,
        now=now,
        last_prompted_at=last_prompted_at,
        reminder_interval_hours=4,
    ):
        return

    await bot.send_message(
        user.telegram_user_id,
        "How's your mood today?",
        reply_markup=_mood_keyboard(),
    )
    await self._user_store.update_single_setting(
        user.telegram_user_id,
        "last_mood_prompt_at",
        now.isoformat(),
    )
    await self._user_store.update_single_setting(
        user.telegram_user_id,
        "last_mood_prompt_note",
        note_key,
    )
```

### 12.5 Startup Message

```python
async def send_startup_message(self, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send startup greeting to all active users."""
    users = await self._user_store.list_active_users()
    for user in users:
        try:
            await context.bot.send_message(
                user.telegram_user_id, STARTUP_MESSAGE
            )
        except (OSError, TelegramError):
            LOGGER.exception(
                "Failed to send startup greeting to user=%s",
                user.telegram_user_id,
            )
```

### 12.6 Job Registration

```python
def register_jobs(self, job_queue: JobQueue) -> None:
    """Register periodic polling jobs."""
    job_queue.run_repeating(
        self._poll_daily_brief,
        interval=60,
        first=60,
        name="daily-brief-poll",
    )
    job_queue.run_repeating(
        self._poll_mood_timers,
        interval=300,
        first=300,
        name="mood-timer-poll",
    )
    job_queue.run_repeating(
        self.dispatch_storage_flush_notifications,
        interval=FLUSH_NOTIFY_SECONDS,
        first=FLUSH_NOTIFY_SECONDS,
    )
    job_queue.run_once(self.send_startup_message, when=0, name="startup-hello")
```

### 12.7 What Gets Removed

| Removed | Replacement |
|---------|-------------|
| `active_chats` set in `bot_data` (`bot.py:359`) | `UserStore.get_mood_check_eligible()` |
| `_get_active_chats()` method (`bot.py:357-364`) | Removed |
| `ACTIVE_CHATS_KEY` constant (`bot.py:83`) | Removed |
| `_reschedule_daily_brief()` method (`bot.py:424-445`) | Removed (no per-user jobs) |
| `send_daily_brief()` fan-out (`bot.py:1303-1319`) | `_poll_daily_brief()` |
| `check_mood_timers()` (`bot.py:1202-1240`) | `_poll_mood_timers()` |
| Per-chat `LAST_PROMPT_AT_KEY` / `LAST_PROMPT_NOTE_KEY` | `settings.last_mood_prompt_at` / `settings.last_mood_prompt_note` in DB |
| `_apply_runtime_config` → `_reschedule_daily_brief` call (`bot.py:460-462`) | Removed |

---

## 13. Registration & Onboarding Flow

### 13.1 New File: `src/telejournal/bot_onboarding.py`

```python
"""Registration and onboarding flow for new users."""

from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

LOGGER = logging.getLogger(__name__)

ONBOARDING_CALLBACK_PREFIX = "onboarding:"
GITHUB_SETUP_STATES = {
    "awaiting_repo": "awaiting_repo",
    "awaiting_token": "awaiting_token",
}
```

### 13.2 `/start` Handler (SaaS Mode Only)

```python
async def start_command(
    self, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /start — register new user or show existing status."""
    user = update.effective_user
    if not user or not update.effective_message:
        return

    if await self._user_store.user_exists(user.id):
        await update.effective_message.reply_text(
            "You're already registered! Send /setup to configure your journal storage."
        )
        return

    await self._user_store.create_user(user.id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("GitHub", callback_data=f"{ONBOARDING_CALLBACK_PREFIX}select:github_repo"),
            InlineKeyboardButton("OneDrive", callback_data=f"{ONBOARDING_CALLBACK_PREFIX}select:onedrive"),
        ],
        [
            InlineKeyboardButton("Google Drive", callback_data=f"{ONBOARDING_CALLBACK_PREFIX}select:google_drive"),
        ],
    ])

    await update.effective_message.reply_text(
        f"Welcome to Telejournal, {user.first_name}! 🎉\n\n"
        "I'll help you set up your personal journal.\n"
        "First, choose where you'd like to store your notes:",
        reply_markup=keyboard,
    )
```

### 13.3 Provider Selection Callback

```python
async def onboarding_callback(
    self, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle onboarding callback queries."""
    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user:
        return

    data = query.data.removeprefix(ONBOARDING_CALLBACK_PREFIX)

    if data.startswith("select:"):
        provider = data.removeprefix("select:")
        await self._user_store.update_storage(user.id, provider, {})
        self._repositories.pop(user.id, None)  # Invalidate repo cache on storage change
        await self._handle_provider_selected(query, provider)
    elif data == "test_connection":
        await self._test_github_connection(query, user.id)
    elif data == "complete_setup":
        await self._complete_setup(query, user.id)
```

### 13.3a `_complete_setup` Helper

```python
async def _complete_setup(self, query, user_id: int) -> None:
    """Mark onboarding complete and confirm to user."""
    await self._user_store.complete_onboarding(user_id)
    self._repositories.pop(user_id, None)  # Invalidate repo cache
    # Update in-memory user cache for self-hosted auth
    if user_id in self._users:
        self._users[user_id].onboarding_complete = True
    await query.edit_message_text(
        "✅ Setup complete! You can now send journal entries."
    )
```

### 13.4 GitHub Setup Flow

```python
async def _handle_provider_selected(self, query, provider: str) -> None:
    """Show provider-specific setup instructions."""
    if provider == "github_repo":
        await query.edit_message_text(
            "Let's set up GitHub storage.\n\n"
            "Step 1: Send me your GitHub repository in the format:\n"
            "`owner/repo`\n\n"
            "Example: `johndoe/my-journal`",
            parse_mode="Markdown",
        )
        # Set state to await repo input
        # (tracked via user settings or a dedicated state column)

    elif provider in ("onedrive", "google_drive"):
        # Start device code OAuth flow
        # (reuse existing code from onedrive.py/google_drive.py)
        await self._start_oauth_flow(query, query.from_user.id, provider)
```

### 13.5 GitHub Connection Test

```python
async def _test_github_connection(self, query, user_id: int) -> None:
    """Test GitHub connection with stored credentials."""
    user = await self._user_store.get_user(user_id)
    if not user:
        return

    config = self._user_store.decrypt_storage_config(
        user.storage_provider, user.storage_config
    )

    try:
        repo = GitHubRepository(
            owner=config["owner"],
            repo=config["repo"],
            token=config["token"],
        )
        # Make a lightweight API call to verify access
        today = datetime.now(UTC)
        await repo.get_note_content(today)
        await query.edit_message_text("✅ Connection successful! Your journal is ready.")
        await self._user_store.complete_onboarding(user_id)
        # Invalidate in-memory caches
        self._repositories.pop(user_id, None)
        if user_id in self._users:
            user.onboarding_complete = True
            self._users[user_id] = user
    except Exception:
        await query.edit_message_text(
            "❌ Could not access the repository. "
            "Please check your credentials and try /setup again."
        )
```

### 13.6 `/setup` Handler (Reconfiguration)

```python
async def setup_command(
    self, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /setup — reconfigure storage provider."""
    auth = await self._check_auth(update)
    if not auth.authorized and auth.reason != "onboarding_incomplete":
        return

    user = update.effective_user
    if not user or not update.effective_message:
        return

    # Show current config and option to change
    db_user = await self._user_store.get_user(user.id)
    current_provider = db_user.storage_provider if db_user else "none"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("GitHub", callback_data=f"{ONBOARDING_CALLBACK_PREFIX}select:github_repo"),
            InlineKeyboardButton("OneDrive", callback_data=f"{ONBOARDING_CALLBACK_PREFIX}select:onedrive"),
        ],
        [
            InlineKeyboardButton("Google Drive", callback_data=f"{ONBOARDING_CALLBACK_PREFIX}select:google_drive"),
        ],
    ])

    await update.effective_message.reply_text(
        f"Current storage: {current_provider}\n\n"
        "Choose a new storage provider to reconfigure:",
        reply_markup=keyboard,
    )
```

### 13.7 Changes to `bot.py` `register_handlers`

```python
def register_handlers(self, application: Application) -> None:
    # ... existing handlers ...

    # Onboarding — /start is SaaS-only; /setup is available whenever
    # UserStore is present (self-hosted family members also need it)
    if self._settings.self_registration:
        application.add_handler(CommandHandler("start", self.start_command))

    if self._user_store is not None:
        application.add_handler(CommandHandler("setup", self.setup_command))
        from telejournal.bot_onboarding import ONBOARDING_CALLBACK_PREFIX
        application.add_handler(
            CallbackQueryHandler(
                self.onboarding_callback,
                pattern=f"^{ONBOARDING_CALLBACK_PREFIX}",
            )
        )
```

### 13.8 Changes to `command_registry.py`

```python
# Add /start and /setup to command specs
START_SPEC = CommandSpec(
    command="start",
    callback_name="start_command",
    help_line="Register and set up your journal",
    saas_only=True,
)
SETUP_SPEC = CommandSpec(
    command="setup",
    callback_name="setup_command",
    help_line="Configure your journal storage",
    saas_only=False,  # Available in self-hosted family mode too
)
```

---

## 14. Per-User Runtime Config

### 14.1 What Changes

Currently `runtime_config.py` modifies a global `Settings` instance and persists to YAML. In the new architecture:

- Per-user settings live in `users.settings` JSON column
- Queried settings (`daily_brief_time_utc`, `prompt_for_mood_if_missing`) are also denormalized as real columns for indexed polling
- `/settings` operates on the requesting user's settings only
- No more YAML persistence for per-user settings

### 14.2 New Functions

```python
def validate_setting_value(key: str, value: Any) -> Any:
    """Validate and coerce a setting value.

    Raises ``ValueError`` if the value is invalid for the given key.
    """
    if key == "daily_brief_time_utc":
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            raise ValueError("Format: HH:MM (e.g., 09:00)")
        hour, minute = value.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError("Hour must be 0-23, minute must be 0-59")
        return value
    if key == "prompt_for_mood_if_missing":
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    if key == "message_timestamp_window_seconds":
        v = int(value)
        if v < 0:
            raise ValueError("Must be non-negative")
        return v
    if key == "tag_choices":
        if isinstance(value, str):
            return [t.strip() for t in value.split(",")]
        return list(value)
    return value

async def apply_user_setting(
    user_store: UserStore,
    user_id: int,
    key: str,
    value: Any,
) -> tuple[dict[str, Any], str]:
    """Apply a setting to a specific user. Returns (updated_settings, message)."""
    supported_keys = {
        "tag_choices",
        "daily_brief_time_utc",
        "prompt_for_mood_if_missing",
        "message_timestamp_window_seconds",
    }
    if key not in supported_keys:
        raise ValueError(f"Unsupported setting: {key}")

    validated_value = validate_setting_value(key, value)
    updated = await user_store.update_single_setting(user_id, key, validated_value)
    return updated, f"Updated {key} to {validated_value}"

def format_user_settings_summary(user_settings: dict[str, Any]) -> str:
    """Render per-user settings for display."""
    lines = []
    for key in sorted(DEFAULT_USER_SETTINGS):
        value = user_settings.get(key, DEFAULT_USER_SETTINGS[key])
        lines.append(f"• {key}: {value}")
    return "\n".join(lines)
```

### 14.3 Settings Keyboard Changes

The `/settings` keyboard shows the same 4 options, but now each user sees and modifies their own values:

```python
async def settings_command(self, update, context):
    auth = await self._check_auth(update)
    if not auth.authorized:
        return

    user_settings = await self._user_store.get_settings(auth.user_id)
    summary = format_user_settings_summary(user_settings)
    keyboard = self._config_keyboard()

    await update.effective_message.reply_text(
        f"Your current settings:\n\n{summary}",
        reply_markup=keyboard,
    )
```

### 14.4 `persist_runtime_settings` Changes

- **Self-hosted:** Can still persist system-level settings to YAML (log_level, bot_menu_enabled)
- **SaaS:** No YAML persistence. Per-user settings are in DB. System-level settings are env vars.

### 14.5 Per-User Settings Cache

Reading `users.settings` from SQLite on every `/settings` command or mood-check is wasteful. The `UserStore` (Section 7.2) includes an in-memory TTL cache:

```
self._settings_cache: TTLCache[int, dict[str, Any]] = TTLCache(
    maxsize=4096, ttl=300  # 5-minute TTL
)
```

- `get_settings()` checks the cache before hitting the DB
- `update_single_setting()` and `update_settings()` invalidate the cache on write
- **Cache policy:** 5-minute TTL is acceptable because settings changes propagate within one poll cycle (60s daily brief / 300s mood check). On `/settings` command the user sees their own update immediately because the cache is invalidated on write.

---

## 15. Startup Wiring

### 15.1 Changes to `main.py`

```python
def _start_bot(telegram_token: str, settings: Settings) -> None:
    """Create and run the Telegram polling application."""
    journal_bot: JournalBot | None = None
    user_store: UserStore | None = None

    async def post_init(application: Application) -> None:
        """Initialize UserStore and bot state inside the polling loop.

        All async DB work happens here so the engine is bound to the
        same event loop that ``run_polling()`` creates.
        """
        nonlocal journal_bot, user_store

        database_url = settings.database_url
        encryptor = None
        encryption_key = os.getenv("TELEJOURNAL_ENCRYPTION_KEY")
        if encryption_key:
            encryptor = TokenEncryptor(encryption_key)
        elif settings.self_registration:
            raise RuntimeError(
                "TELEJOURNAL_ENCRYPTION_KEY is required in SaaS mode"
            )

        user_store = UserStore(database_url, encryptor=encryptor)
        await user_store.initialize()

        if not settings.self_registration and settings.allowed_user_ids:
            default_storage = _extract_default_storage(settings)
            await user_store.sync_allowed_users(
                settings.allowed_user_ids,
                default_storage,
                settings.storage_provider,
            )

        journal_bot = JournalBot(settings, user_store=user_store)

        # Populate in-memory user cache for self-hosted fast path
        if not settings.self_registration:
            users = await user_store.list_active_users()
            journal_bot._users = {u.telegram_user_id: u for u in users}

        journal_bot.register_handlers(application)
        if application.job_queue is None:
            raise RuntimeError("Job queue is unavailable; install job-queue extras")
        journal_bot.register_jobs(application.job_queue)

    app_instance = (
        Application.builder().token(telegram_token).post_init(post_init).build()
    )
    app_instance.bot_data[SETTINGS_BOT_DATA_KEY] = settings

    try:
        app_instance.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        async def _shutdown() -> None:
            if journal_bot is not None:
                await journal_bot.shutdown()
            if user_store is not None:
                await user_store.close()
        asyncio.run(_shutdown())
```

> **Note:** `post_init` runs inside the event loop created by `run_polling()`, so the `create_async_engine()` in `UserStore.__init__` is bound to the correct loop. No `asyncio.run()` calls outside the loop.

### 15.2 `_extract_default_storage` Helper

Uses a config-driven field mapping (`_STORAGE_FIELD_MAP`) to flatten provider config, avoiding a long if/elif chain.

```python
# Provider-specific field mapping: Settings field → storage_config key
_STORAGE_FIELD_MAP: dict[str, dict[str, str]] = {
    "obsidian_vault": {
        "root": "vault_root",              # Path → str
        "secure_file_permissions": "secure_file_permissions",
    },
    "github_repo": {
        "owner": "github_owner",
        "repo": "github_repo",
        "token": "github_token",
        "branch": "github_branch",
        "path_prefix": "github_path_prefix",
        "api_base_url": "github_api_base_url",
        "batch_window_seconds": "github_batch_window_seconds",
    },
    "onedrive": {
        "tenant_id": "onedrive_tenant_id",
        "client_id": "onedrive_client_id",
        "client_secret": "onedrive_client_secret",
        "root_path": "onedrive_root_path",
        "api_base_url": "onedrive_api_base_url",
        "batch_window_seconds": "onedrive_batch_window_seconds",
        "access_token": "onedrive_access_token",
        "refresh_token": "onedrive_refresh_token",
        "token_expires_at_utc": "onedrive_token_expires_at_utc",
    },
    "google_drive": {
        "client_id": "google_drive_client_id",
        "client_secret": "google_drive_client_secret",
        "folder_id": "google_drive_folder_id",
        "batch_window_seconds": "google_drive_batch_window_seconds",
        "access_token": "google_drive_access_token",
        "refresh_token": "google_drive_refresh_token",
        "token_expires_at_utc": "google_drive_token_expires_at_utc",
    },
}

def _extract_default_storage(settings: Settings) -> dict[str, Any]:
    """Extract default storage config from Settings for user seeding."""
    provider = settings.storage_provider
    field_map = _STORAGE_FIELD_MAP.get(provider, {})
    config: dict[str, Any] = {}
    for config_key, settings_field in field_map.items():
        value = getattr(settings, settings_field, None)
        if value is not None:
            # Convert Path to string for JSON storage
            if isinstance(value, Path):
                value = str(value)
            config[config_key] = value
    return config
```

### 15.3 JournalBot Constructor Changes

```python
class JournalBot:
    def __init__(self, settings: Settings, user_store: UserStore | None = None) -> None:
        self._settings = settings
        self._user_store = user_store
        self._repository = build_repository(settings)  # Legacy single-user fallback
        self._repositories: dict[int, Any] = {}  # Per-user repository cache
        self._users: dict[int, User] = {}  # In-memory user cache (self-hosted only)

        # ... rest of constructor unchanged, but service classes receive
        # user_id-aware repository providers
```

---

## 16. Dockerfile & Deployment

### 16.1 New Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_TOKEN` | Yes | — | Bot token from @BotFather |
| `TELEJOURNAL_SELF_REGISTRATION` | No | `false` | Enable self-registration (SaaS mode) |
| `TELEJOURNAL_DATABASE_URL` | No | `sqlite+aiosqlite:///telejournal.db` | Database URL |
| `TELEJOURNAL_ENCRYPTION_KEY` | SaaS: Yes, Self-hosted: No | — | Fernet encryption key for tokens |
| `TELEGRAM_ALLOWED_USER_IDS` | Self-hosted: Yes, SaaS: No | — | Comma-separated Telegram user IDs |

### 16.2 Dockerfile Changes

```dockerfile
# Add to runtime stage
ENV TELEJOURNAL_DATABASE_URL="sqlite+aiosqlite:///telejournal/data/telejournal.db"
VOLUME ["/telejournal/data"]
```

### 16.3 Docker Compose Example

```yaml
version: "3.8"
services:
  telejournal:
    build: .
    environment:
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - TELEJOURNAL_SELF_REGISTRATION=true
      - TELEJOURNAL_DATABASE_URL=sqlite+aiosqlite:///telejournal/data/telejournal.db
      - TELEJOURNAL_ENCRYPTION_KEY=${TELEJOURNAL_ENCRYPTION_KEY}
    volumes:
      - telejournal-data:/telejournal/data

volumes:
  telejournal-data:
```

---

## 17. Test Strategy

### 17.1 New Test Files

| File | Covers |
|------|--------|
| `tests/test_models.py` | SQLAlchemy model creation, JSON field handling |
| `tests/test_user_store.py` | CRUD operations, sync, settings merge, encryption roundtrip |
| `tests/test_crypto.py` | Fernet encrypt/decrypt, dict operations, invalid token handling |
| `tests/test_write_buffer.py` | SqliteWritesRepository audit trail, token refresh injection, replay audit |
| `tests/test_auth_unified.py` | Self-hosted whitelist, SaaS registration, onboarding gate |
| `tests/test_per_user_repository.py` | Different users get different repos, cache behavior, fast path |
| `tests/test_onboarding.py` | /start, provider selection, GitHub setup, OAuth flow |
| `tests/test_jobs_polling.py` | Daily brief eligible query, mood timer eligible query, batch processing |

### 17.2 Modified Test Files

| File | Changes |
|------|---------|
| `tests/test_main.py` | Add SaaS mode `run_command` tests with UserStore mock |
| `tests/test_bot.py` | Handler tests with `_check_auth`, `_get_repository` |
| `tests/test_config.py` | `self_registration`, `database_url` validation |
| `tests/test_runtime_config.py` | Per-user settings tests (DB-backed) |
| `tests/test_storage.py` | `build_repository_from_config` tests |

### 17.3 Test Pattern: In-Memory SQLite

All tests use an in-memory SQLite database:

```python
@pytest.fixture
async def user_store():
    store = UserStore("sqlite+aiosqlite://", encryptor=None)
    await store.initialize()
    yield store
    await store.close()

@pytest.fixture
async def user_store_encrypted():
    encryptor = TokenEncryptor("test-key-for-testing")
    store = UserStore("sqlite+aiosqlite://", encryptor=encryptor)
    await store.initialize()
    yield store
    await store.close()
```

### 17.4 Backward Compatibility

All existing tests must pass unchanged. The default configuration (`self_registration=False`, `UserStore` not passed to `JournalBot`) preserves current behavior. The single-user fast path in `_get_repository` (populated from `_users` cache on first call) ensures zero performance regression for self-hosted mode.

---

## 18. Implementation Order

### Phase 1: Foundation (No Behavioral Change)

| Step | Files | Description |
|------|-------|-------------|
| 1.1 | `pyproject.toml` | Add dependencies (sqlalchemy, aiosqlite, cryptography) |
| 1.2 | `src/telejournal/models.py` | SQLAlchemy models (User, PendingWrite) |
| 1.3 | `src/telejournal/crypto.py` | Fernet encryptor |
| 1.4 | `src/telejournal/user_store.py` | UserStore with all methods |
| 1.5 | `tests/test_models.py`, `tests/test_user_store.py`, `tests/test_crypto.py` | Tests for foundation |

### Phase 2: Settings & Config

| Step | Files | Description |
|------|-------|-------------|
| 2.1 | `src/telejournal/config/models.py` | Add `self_registration`, `database_url` to Settings |
| 2.2 | `src/telejournal/config/constants.py` | Add defaults |
| 2.3 | `src/telejournal/config/resolver.py` | SaaS mode skips validation |
| 2.4 | `src/telejournal/config_loader.py` | New env var mappings |
| 2.5 | `tests/test_config.py` | New field tests |

### Phase 3: Startup Wiring

| Step | Files | Description |
|------|-------|-------------|
| 3.1 | `src/telejournal/main.py` | Initialize UserStore, sync, pass to JournalBot |
| 3.2 | `src/telejournal/bot.py` | Accept `user_store` param, add `_get_repository` |
| 3.3 | `tests/test_main.py` | SaaS mode startup tests |

### Phase 4: Auth & Per-User Repos

| Step | Files | Description |
|------|-------|-------------|
| 4.1 | `src/telejournal/bot.py` | `_check_auth` replacing `_is_private_and_authorized` |
| 4.2 | `src/telejournal/bot.py` | `_get_repository` with cache and fast path |
| 4.3 | `src/telejournal/storage/factory.py` | `build_repository_from_config` |
| 4.4 | `src/telejournal/bot_commands.py` | Update constructor and handler guards |
| 4.5 | `src/telejournal/bot_setdate.py` | Update constructor and handler guards |
| 4.6 | `src/telejournal/bot_delivery.py` | Change `repository_provider` signature |
| 4.7 | `src/telejournal/bot_media.py` | Change `repository_provider` signature |
| 4.8 | `src/telejournal/bot_callbacks.py` | Change `repository_provider` signature |
| 4.9 | `tests/test_auth_unified.py`, `tests/test_per_user_repository.py` | Auth and repo tests |

### Phase 5: Job Management

| Step | Files | Description |
|------|-------|-------------|
| 5.1 | `src/telejournal/bot.py` | `_poll_daily_brief`, `_poll_mood_timers` |
| 5.2 | `src/telejournal/bot.py` | Remove `active_chats`, `_reschedule_daily_brief` |
| 5.3 | `src/telejournal/bot.py` | Update `register_jobs` |
| 5.4 | `tests/test_jobs_polling.py` | Polling job tests |

### Phase 6: Onboarding

| Step | Files | Description |
|------|-------|-------------|
| 6.1 | `src/telejournal/bot_onboarding.py` | `/start`, `/setup`, provider callbacks |
| 6.2 | `src/telejournal/bot.py` | Register onboarding handlers |
| 6.3 | `src/telejournal/command_registry.py` | Add /start, /setup specs |
| 6.4 | `tests/test_onboarding.py` | Onboarding flow tests |

### Phase 7: Per-User Config

| Step | Files | Description |
|------|-------|-------------|
| 7.1 | `src/telejournal/runtime_config.py` | `apply_user_setting`, `format_user_settings_summary` |
| 7.2 | `src/telejournal/bot.py` | Update `/settings` handler to use per-user settings |
| 7.3 | `src/telejournal/bot_callbacks.py` | Update config callback routing |
| 7.4 | `tests/test_runtime_config.py` | Per-user settings tests |

### Phase 8: Write Buffer

| Step | Files | Description |
|------|-------|-------------|
| 8.1 | `src/telejournal/write_buffer.py` | `SqliteWritesRepository` decorator + audit trail |
| 8.2 | `src/telejournal/storage/factory.py` | Add `build_saas_repository` wrapping providers with `SqliteWritesRepository` |
| 8.3 | `src/telejournal/bot.py` | Wire `SqliteWritesRepository` into `_get_repository` for SaaS mode |
| 8.4 | `tests/test_write_buffer.py` | Audit trail, token refresh injection, replay audit tests |

### Phase 9: Cleanup & Polish

| Step | Files | Description |
|------|-------|-------------|
| 9.1 | `src/telejournal/bot.py` | Remove dead code (active_chats, etc.) |
| 9.2 | `src/telejournal/onedrive.py`, `google_drive.py` | Sanitize OAuth error messages |
| 9.3 | `src/telejournal/bot.py` | Downgrade chat_id logging to DEBUG |
| 9.4 | `Dockerfile` | Add volume, env vars |
| 9.5 | All test files | Ensure 100% coverage |

---

## 19. Known Limitations (Phase 1)

| Limitation | Impact | Phase 2 Mitigation |
|-----------|--------|-------------------|
| Single-instance only | Duplicate jobs if 2 processes run | Add leader election (SQLite advisory lock) |
| SIGKILL loses buffered writes | Self-hosted: in-memory buffer loses writes; SaaS: SQLite audit trail records them but does not auto-replay | Accept for self-hosted; SaaS users can check audit table and recover from provider |
| Ephemeral per-chat state | `/setdate` override, album buffer reset on restart | Persist to DB |
| `urllib` blocking HTTP | 40-thread pool cap | Replace with `httpx` async |
| Source-note links break on restart | Cosmetic degradation | Persist `_reply_source_notes` to DB |
| Flush notifications lost on restart | Cosmetic degradation | Persist pending flush chat IDs to DB |
| No Alembic migrations | Schema changes require manual scripts | Add Alembic in Phase 2 |
| Token encryption is Fernet-only | Single cipher | Support multiple ciphers if needed |

---

## 20. SaaS Anti-Pattern Findings & Mitigations

| # | Finding | Severity | Mitigation in This Plan |
|---|---------|----------|------------------------|
| 1 | `active_chats` in-memory | 🔴 Critical | Replaced with `UserStore` DB queries |
| 2 | No job coordination | 🔴 Critical | Single-instance documented; leader election deferred |
| 3 | In-memory write buffer | 🔴 Critical | `SqliteWriteBuffer` for SaaS mode |
| 4 | Plaintext tokens | 🔴 Critical | Fernet encryption in `TokenEncryptor` |
| 5 | `urllib` blocking HTTP | 🔴 Critical | Deferred to Phase 2 (httpx migration) |
| 6 | Process-local locks | 🟡 Moderate | Acceptable single-instance |
| 7 | Non-atomic YAML persist | 🟡 Moderate | Per-user settings in DB; YAML only for system config |
| 8 | `_reply_source_notes` local | 🟡 Moderate | Acceptable cosmetic degradation |
| 9 | `_pending_flush_chat_ids` local | 🟡 Moderate | Acceptable cosmetic degradation |
| 10 | Album buffering local | 🟡 Moderate | Low probability, acceptable |
| 11 | OAuth error bodies in exceptions | 🟡 Moderate | Sanitize in Phase 9 cleanup |
| 12 | `threading.Lock` + asyncio | 🟡 Moderate | Acceptable; refactor in Phase 2 |
| 13 | Chat IDs in logs | ⚪ Low | Downgrade to DEBUG in Phase 9 |
| 14 | Temp-file media fallback | ⚪ Low | Accept for Phase 1 |
| 15 | Ephemeral chat_data state | ⚪ Low | Persist key fields to DB |
| 16 | Non-atomic token persist | ⚪ Low | Tokens in DB with SQLAlchemy (atomic by default) |

---

## Estimated Scope

| Category | Estimated Lines |
|----------|----------------|
| New modules (models, user_store, crypto, write_buffer, onboarding) | ~800-1000 |
| Modified modules (bot.py, main.py, service classes, config, factory) | ~500-600 |
| New tests | ~800-1000 |
| Existing test adjustments | ~50-80 |
| **Total** | **~2200-2700** |
