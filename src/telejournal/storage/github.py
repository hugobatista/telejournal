"""GitHub repository storage provider."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import yaml
from telegram import PhotoSize, Video, VideoNote, Voice

from telejournal.formatting import marker_end_comment, marker_start_comment

from .common import LOGGER, NoteData, PendingWrite, _TIMESTAMP_RE


class GitHubRepository:
    """Persist journal notes in a GitHub repository via the REST API."""

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        branch: str = "main",
        path_prefix: str = "",
        api_base_url: str = "https://api.github.com",
        batch_window_seconds: int = 60,
    ) -> None:
        """Initialize a GitHub repository-backed storage provider."""
        self._owner = owner.strip()
        self._repo = repo.strip()
        self._token = token.strip()
        self._branch = branch.strip() or "main"
        self._path_prefix = path_prefix.strip("/")
        self._api_base_url = api_base_url.rstrip("/")
        self._batch_window_seconds = max(1, int(batch_window_seconds))
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._queue_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._pending_puts: dict[str, PendingWrite] = {}
        self._pending_deletes: set[str] = set()
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_cycle = 0
        self._warn_if_repository_is_public()

    async def _pending_write_for_path(
        self,
        repo_path: str,
    ) -> PendingWrite | None:
        """Return queued write for one path when present."""
        async with self._queue_lock:
            return self._pending_puts.get(repo_path)

    async def _is_pending_delete(self, repo_path: str) -> bool:
        """Return whether one path is queued for deletion."""
        async with self._queue_lock:
            return repo_path in self._pending_deletes

    def _ensure_flush_task(self) -> None:
        """Start the periodic flush worker once the queue becomes active."""
        if self._flush_task is not None and not self._flush_task.done():
            return

        self._flush_task = asyncio.create_task(
            self._flush_loop(),
            name="telejournal-github-flush",
        )
        LOGGER.info(
            "Started GitHub batch flush worker for %s/%s (window=%ss)",
            self._owner,
            self._repo,
            self._batch_window_seconds,
        )

    async def _flush_loop(self) -> None:
        """Flush queued writes at a fixed interval in the background."""
        while True:
            try:
                await asyncio.sleep(self._batch_window_seconds)
                await self.flush_pending(reason="timer")
            except asyncio.CancelledError:
                LOGGER.info(
                    "Stopped GitHub batch flush worker for %s/%s",
                    self._owner,
                    self._repo,
                )
                raise
            except Exception:
                LOGGER.exception(
                    "Unexpected failure in GitHub batch flush worker for %s/%s",
                    self._owner,
                    self._repo,
                )

    async def _queue_put_content(
        self,
        repo_path: str,
        payload_bytes: bytes,
        message: str,
    ) -> None:
        """Queue one create/update operation for a future batch flush."""
        async with self._queue_lock:
            self._pending_deletes.discard(repo_path)
            self._pending_puts[repo_path] = PendingWrite(
                payload=payload_bytes,
                message=message,
            )
            pending_total = len(self._pending_puts) + len(self._pending_deletes)

        self._ensure_flush_task()
        LOGGER.debug(
            "Queued GitHub upsert for %s (pending items=%d)",
            repo_path,
            pending_total,
        )

    async def _queue_delete_content(self, repo_path: str) -> None:
        """Queue one delete operation for a future batch flush."""
        async with self._queue_lock:
            self._pending_puts.pop(repo_path, None)
            self._pending_deletes.add(repo_path)
            pending_total = len(self._pending_puts) + len(self._pending_deletes)

        self._ensure_flush_task()
        LOGGER.debug(
            "Queued GitHub delete for %s (pending items=%d)",
            repo_path,
            pending_total,
        )

    def _flush_put_content(self, repo_path: str, pending: PendingWrite) -> None:
        """Flush one queued write by resolving latest remote sha first."""
        existing = self._get_content(repo_path)
        sha = None
        if existing is not None:
            resolved = str(existing.get("sha") or "")
            sha = resolved or None

        self._put_content(
            repo_path,
            pending.payload,
            pending.message,
            sha,
        )

    def _flush_delete_content(self, repo_path: str) -> None:
        """Flush one queued delete, skipping when path does not exist remotely."""
        existing = self._get_content(repo_path)
        if existing is None:
            return

        sha = str(existing.get("sha") or "")
        if not sha:
            raise RuntimeError(f"Could not resolve sha for delete path: {repo_path}")

        self._delete_content(repo_path, sha)

    async def flush_pending(self, reason: str = "manual") -> None:
        """Flush all pending queue items and retry failures on next batch."""
        async with self._flush_lock:
            async with self._queue_lock:
                pending_puts = dict(self._pending_puts)
                pending_deletes = set(self._pending_deletes)
                self._pending_puts.clear()
                self._pending_deletes.clear()

            if not pending_puts and not pending_deletes:
                LOGGER.debug(
                    "GitHub batch flush skipped for %s/%s (empty queue, reason=%s)",
                    self._owner,
                    self._repo,
                    reason,
                )
                return

            self._flush_cycle += 1
            LOGGER.info(
                (
                    "Flushing GitHub batch #%d for %s/%s "
                    "(%d upserts, %d deletes; reason=%s)"
                ),
                self._flush_cycle,
                self._owner,
                self._repo,
                len(pending_puts),
                len(pending_deletes),
                reason,
            )

            failed_puts: dict[str, PendingWrite] = {}
            failed_deletes: set[str] = set()

            for repo_path in sorted(pending_deletes):
                try:
                    await asyncio.to_thread(self._flush_delete_content, repo_path)
                except Exception:
                    LOGGER.exception(
                        "GitHub batch delete failed for %s",
                        repo_path,
                    )
                    failed_deletes.add(repo_path)

            for repo_path, pending in pending_puts.items():
                try:
                    await asyncio.to_thread(self._flush_put_content, repo_path, pending)
                except Exception:
                    LOGGER.exception(
                        "GitHub batch upsert failed for %s",
                        repo_path,
                    )
                    failed_puts[repo_path] = pending

            if failed_puts or failed_deletes:
                async with self._queue_lock:
                    for repo_path in failed_deletes:
                        if repo_path not in self._pending_puts:
                            self._pending_deletes.add(repo_path)
                    for repo_path, pending in failed_puts.items():
                        if repo_path not in self._pending_deletes:
                            self._pending_puts[repo_path] = pending
                    remaining = len(self._pending_puts) + len(self._pending_deletes)

                LOGGER.warning(
                    (
                        "GitHub batch #%d completed with %d failed upserts "
                        "and %d failed deletes; re-queued pending items=%d"
                    ),
                    self._flush_cycle,
                    len(failed_puts),
                    len(failed_deletes),
                    remaining,
                )
                return

            LOGGER.info(
                "GitHub batch #%d flush completed successfully",
                self._flush_cycle,
            )

    @property
    def vault_root(self) -> Path:
        """Expose a placeholder path for API compatibility with local provider."""
        return Path("/")

    def _warn_if_repository_is_public(self) -> None:
        """Log a warning when the configured repository is publicly visible."""
        try:
            metadata = self._request_json("GET", f"/repos/{self._owner}/{self._repo}")
        except RuntimeError:
            LOGGER.warning(
                "Could not verify GitHub repository visibility for %s/%s",
                self._owner,
                self._repo,
            )
            return

        if isinstance(metadata, dict) and metadata.get("private") is False:
            LOGGER.warning(
                "GitHub storage repository %s/%s is public. "
                "Journal notes and media may be exposed.",
                self._owner,
                self._repo,
            )

    def _build_headers(
        self, accept: str = "application/vnd.github+json"
    ) -> dict[str, str]:
        """Build standard GitHub REST API headers."""
        return {
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "telejournal",
        }

    def _request_json(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any | None:
        """Execute a GitHub API request and parse JSON responses."""
        url = f"{self._api_base_url}{endpoint}"
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        request = urllib_request.Request(
            url=url,
            data=body,
            method=method,
            headers=self._build_headers(),
        )

        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            raise RuntimeError(
                f"GitHub API request failed ({method} {endpoint}): {exc.code}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"GitHub API request failed ({method} {endpoint}): {exc}"
            ) from exc

        if not raw.strip():
            return None
        return json.loads(raw)

    def _repo_path(self, rel_path: str) -> str:
        """Return repository path under the optional provider prefix."""
        normalized = rel_path.replace("\\", "/").strip("/")
        if not self._path_prefix:
            return normalized
        if not normalized:
            return self._path_prefix
        return f"{self._path_prefix}/{normalized}"

    def _note_relpath(self, note_dt: datetime) -> str:
        """Return note path relative to repository root."""
        return f"{note_dt.year}/{note_dt.strftime('%Y-%m-%d')}.md"

    @staticmethod
    def _default_frontmatter(note_dt: datetime) -> dict[str, Any]:
        """Create default YAML frontmatter for a date."""
        today = datetime.now(UTC).date()
        if note_dt.date() == today:
            created_dt = datetime.now(UTC)
        else:
            created_dt = datetime.combine(
                note_dt.date(), datetime.min.time(), tzinfo=UTC
            )

        return {
            "mood": None,
            "location": None,
            "tags": ["journal"],
            "created": created_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """Extract YAML frontmatter and markdown body from note content."""
        if not content.startswith("---\n"):
            return {}, content

        closing_index = content.find("\n---\n", 4)
        if closing_index == -1:
            return {}, content

        raw_yaml = content[4:closing_index]
        body = content[closing_index + 5 :].lstrip("\n")

        try:
            loaded = yaml.safe_load(raw_yaml) or {}
            if not isinstance(loaded, dict):
                raise ValueError("frontmatter is not a mapping")
            return loaded, body
        except Exception:  # pragma: no cover
            LOGGER.exception("YAML parse error, resetting frontmatter")
            return {}, body

    @staticmethod
    def _serialize_note(frontmatter: dict[str, Any], body: str) -> str:
        """Serialize frontmatter and body into markdown file content."""
        rendered_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        rendered_body = body.rstrip()
        if rendered_body:
            rendered_body = f"{rendered_body}\n"

        return f"---\n{rendered_yaml}\n---\n\n{rendered_body}"

    def get_note_path(self, note_dt: datetime) -> Path:
        """Return note-like path object for compatibility with call sites."""
        return Path(self._repo_path(self._note_relpath(note_dt)))

    def _content_endpoint(self, repo_path: str) -> str:
        """Build URL path for the contents endpoint, including branch ref."""
        quoted = urllib_parse.quote(repo_path, safe="/")
        query = urllib_parse.urlencode({"ref": self._branch})
        return f"/repos/{self._owner}/{self._repo}/contents/{quoted}?{query}"

    def _decode_content(self, payload: dict[str, Any]) -> bytes:
        """Decode base64 payload returned by GitHub contents API."""
        raw_content = str(payload.get("content", ""))
        if not raw_content:
            return b""
        return base64.b64decode(raw_content)

    def _get_content(self, repo_path: str) -> dict[str, Any] | None:
        """Fetch repository file metadata/content from GitHub."""
        response = self._request_json(
            "GET",
            self._content_endpoint(repo_path),
            allow_not_found=True,
        )
        if response is None:
            return None
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected GitHub contents payload")
        return response

    def _put_content(
        self,
        repo_path: str,
        payload_bytes: bytes,
        message: str,
        sha: str | None,
    ) -> None:
        """Create or update one repository file through GitHub contents API."""
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(payload_bytes).decode("ascii"),
            "branch": self._branch,
        }
        if sha:
            payload["sha"] = sha

        self._request_json(
            "PUT",
            self._content_endpoint(repo_path),
            payload,
        )

    def _delete_content(self, repo_path: str, sha: str) -> bool:
        """Delete one repository file using GitHub contents API."""
        payload = {
            "message": f"telejournal: delete {repo_path}",
            "sha": sha,
            "branch": self._branch,
        }
        response = self._request_json(
            "DELETE",
            self._content_endpoint(repo_path),
            payload,
            allow_not_found=True,
        )
        return response is not None

    async def _read_note(self, note_path: str) -> tuple[NoteData, str | None]:
        """Read note content from GitHub and return parsed note + file sha."""

        queued = await self._pending_write_for_path(note_path)
        if queued is not None:
            decoded = queued.payload.decode("utf-8")
            frontmatter, body = self._split_frontmatter(decoded)
            return NoteData(frontmatter=frontmatter, body=body), None

        if await self._is_pending_delete(note_path):
            return NoteData(frontmatter={}, body=""), None

        def _read_sync() -> tuple[NoteData, str | None]:
            payload = self._get_content(note_path)
            if payload is None:
                return NoteData(frontmatter={}, body=""), None
            decoded = self._decode_content(payload).decode("utf-8")
            frontmatter, body = self._split_frontmatter(decoded)
            sha = str(payload.get("sha") or "") or None
            return NoteData(frontmatter=frontmatter, body=body), sha

        return await asyncio.to_thread(_read_sync)

    async def _write_note(
        self,
        note_path: str,
        frontmatter: dict[str, Any],
        body: str,
        sha: str | None,
    ) -> None:
        """Queue note content for batched GitHub create-or-update operations."""
        del sha
        rendered = self._serialize_note(frontmatter, body)
        await self._queue_put_content(
            note_path,
            rendered.encode("utf-8"),
            f"telejournal: update {note_path}",
        )

    async def get_note_frontmatter(self, note_dt: datetime) -> dict[str, Any]:
        """Load frontmatter for a note and apply defaults."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        note_data, _sha = await self._read_note(note_path)
        defaults = self._default_frontmatter(note_dt)
        defaults.update(note_data.frontmatter)
        return defaults

    async def append_entry(
        self,
        note_dt: datetime,
        entry: str,
        frontmatter_updates: dict[str, Any] | None = None,
        *,
        as_continuation: bool = False,
    ) -> Path:
        """Append one entry to the remote note and update frontmatter."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        lock = self._locks[note_path]
        async with lock:
            note_data, sha = await self._read_note(note_path)
            frontmatter = self._default_frontmatter(note_dt)
            frontmatter.update(note_data.frontmatter)

            if frontmatter_updates:
                frontmatter.update(frontmatter_updates)

            current_body = note_data.body.rstrip()
            clean_entry = entry.strip()
            if as_continuation and current_body and clean_entry:
                next_body = f"{current_body}\n{clean_entry}"
            else:
                body_parts = [part for part in [current_body, clean_entry] if part]
                next_body = "\n\n".join(body_parts)

            await self._write_note(note_path, frontmatter, next_body, sha)

        return Path(note_path)

    async def update_marked_entry(
        self,
        note_dt: datetime,
        marker: str,
        body: str,
        frontmatter_updates: dict[str, Any] | None = None,
    ) -> bool:
        """Update one marker-delimited entry body, if present."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        lock = self._locks[note_path]
        async with lock:
            note_data, sha = await self._read_note(note_path)
            frontmatter = self._default_frontmatter(note_dt)
            frontmatter.update(note_data.frontmatter)

            if frontmatter_updates:
                frontmatter.update(frontmatter_updates)

            start_marker = marker_start_comment(marker)
            end_marker = marker_end_comment(marker)
            escaped_start = re.escape(start_marker)
            escaped_end = re.escape(end_marker)
            pattern = re.compile(
                rf"{escaped_start}\n.*?\n{escaped_end}",
                re.DOTALL,
            )

            clean_body = body.strip()
            replacement = f"{start_marker}\n{clean_body}\n{end_marker}"
            next_body, count = pattern.subn(replacement, note_data.body, count=1)
            if count == 0:
                return False

            await self._write_note(note_path, frontmatter, next_body, sha)
            return True

    async def update_frontmatter(
        self,
        note_dt: datetime,
        updates: dict[str, Any],
    ) -> Path:
        """Update frontmatter while preserving note body."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        lock = self._locks[note_path]
        async with lock:
            note_data, sha = await self._read_note(note_path)
            frontmatter = self._default_frontmatter(note_dt)
            frontmatter.update(note_data.frontmatter)
            frontmatter.update(updates)
            await self._write_note(note_path, frontmatter, note_data.body, sha)
        return Path(note_path)

    async def get_note_content(self, note_dt: datetime) -> str | None:
        """Return note markdown content for one day if it exists."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        note_data, _sha = await self._read_note(note_path)
        if not note_data.frontmatter and not note_data.body.strip():
            return None
        return self._serialize_note(note_data.frontmatter, note_data.body)

    async def get_same_day_previous_year_notes(
        self,
        reference_dt: datetime,
    ) -> list[tuple[datetime, str]]:
        """Return same-day notes from all previous years in the repository."""

        def _collect_years() -> list[int]:
            root_path = self._repo_path("")
            endpoint = (
                self._content_endpoint(root_path)
                if root_path
                else (
                    f"/repos/{self._owner}/{self._repo}/contents/?"
                    f"{urllib_parse.urlencode({'ref': self._branch})}"
                )
            )
            payload = self._request_json("GET", endpoint, allow_not_found=True)
            if payload is None or not isinstance(payload, list):
                return []

            years: list[int] = []
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "dir":
                    continue
                name = str(entry.get("name") or "")
                if not name.isdigit():
                    continue
                year = int(name)
                if year < reference_dt.year:
                    years.append(year)

            years.sort()
            return years

        years = await asyncio.to_thread(_collect_years)
        results: list[tuple[datetime, str]] = []
        target_mm_dd = reference_dt.strftime("%m-%d")
        for year in years:
            note_path = self._repo_path(f"{year}/{year}-{target_mm_dd}.md")
            note_data, _sha = await self._read_note(note_path)
            if not note_data.frontmatter and not note_data.body.strip():
                continue
            rendered = self._serialize_note(note_data.frontmatter, note_data.body)
            note_dt = datetime(
                year,
                reference_dt.month,
                reference_dt.day,
                tzinfo=UTC,
            )
            results.append((note_dt, rendered))

        return results

    async def delete_last_entry(self, note_dt: datetime) -> str | None:
        """Delete the last body entry block and return removed content."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        lock = self._locks[note_path]
        async with lock:
            note_data, sha = await self._read_note(note_path)
            body = note_data.body.strip()
            if not body:
                return None

            entries = [entry for entry in body.split("\n\n") if entry.strip()]
            removed = entries.pop().strip()
            next_body = "\n\n".join(entries)
            await self._write_note(note_path, note_data.frontmatter, next_body, sha)
            return removed

    async def peek_last_entry(self, note_dt: datetime) -> str | None:
        """Return last body entry block without mutating the note."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        note_data, _sha = await self._read_note(note_path)
        body = note_data.body.strip()
        if not body:
            return None

        entries = [entry for entry in body.split("\n\n") if entry.strip()]
        return entries[-1].strip()

    async def delete_day(self, note_dt: datetime) -> bool:
        """Delete full day note file and return whether it existed."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        lock = self._locks[note_path]
        async with lock:
            queued = await self._pending_write_for_path(note_path)
            if queued is not None:
                await self._queue_delete_content(note_path)
                return True

            if await self._is_pending_delete(note_path):
                return False

            payload = await asyncio.to_thread(self._get_content, note_path)
            if payload is None:
                return False

            await self._queue_delete_content(note_path)
            return True

    async def note_has_entry(self, note_dt: datetime) -> bool:
        """Return whether a note contains at least one body entry."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        note_data, _sha = await self._read_note(note_path)
        return bool(note_data.body.strip())

    async def note_has_mood(self, note_dt: datetime) -> bool:
        """Return whether a note has mood set to a non-null value."""
        frontmatter = await self.get_note_frontmatter(note_dt)
        mood = frontmatter.get("mood")
        if isinstance(mood, int):
            return True
        if isinstance(mood, list):
            for item in mood:
                if isinstance(item, dict) and isinstance(item.get("value"), int):
                    return True
            return False
        if isinstance(mood, dict):
            return isinstance(mood.get("value"), int)
        return mood is not None

    async def get_last_entry_time(
        self,
        note_dt: datetime,
    ) -> datetime | None:
        """Infer last entry timestamp from note body timestamped lines."""
        note_path = self._repo_path(self._note_relpath(note_dt))
        note_data, _sha = await self._read_note(note_path)
        if not note_data.body.strip():
            return None

        last_time: datetime | None = None
        for line in note_data.body.splitlines():
            match = _TIMESTAMP_RE.match(line.strip())
            if not match:
                continue

            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            second = int(match.group("second") or 0)
            parsed = datetime.combine(
                note_dt.date(),
                datetime.min.time().replace(
                    hour=hour,
                    minute=minute,
                    second=second,
                ),
            ).replace(tzinfo=UTC)
            last_time = parsed

        return last_time

    async def _download_media_bytes(self, media: Any) -> bytes:
        """Download one Telegram media payload and return bytes."""
        tg_file = await media.get_file()
        download_as_bytearray = getattr(tg_file, "download_as_bytearray", None)
        if callable(download_as_bytearray):
            payload = await download_as_bytearray()
            return bytes(payload)

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                temp_path = Path(tmp.name)
            await tg_file.download_to_drive(temp_path)
            return await asyncio.to_thread(temp_path.read_bytes)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    async def _save_media(self, note_dt: datetime, ts: str, suffix: str) -> str:
        """Reserve the next available attachment filename in repository tree."""
        counter = 0
        while True:
            filename = f"{ts}{suffix}" if counter == 0 else f"{ts}_{counter}{suffix}"
            rel_path = f"{note_dt.year}/attachments/{filename}"
            repo_path = self._repo_path(rel_path)
            if await self._is_pending_delete(repo_path):
                return rel_path

            queued = await self._pending_write_for_path(repo_path)
            if queued is not None:
                counter += 1
                continue

            payload = await asyncio.to_thread(self._get_content, repo_path)
            if payload is None:
                return rel_path
            counter += 1

    async def save_photo(
        self,
        photo: PhotoSize,
        note_dt: datetime,
        ts: str,
    ) -> str:
        """Upload a photo and return attachment relative path."""
        rel_path = await self._save_media(note_dt, ts, ".jpg")
        payload = await self._download_media_bytes(photo)
        await self._queue_put_content(
            self._repo_path(rel_path),
            payload,
            f"telejournal: add {rel_path}",
        )
        return rel_path

    async def save_voice(
        self,
        voice: Voice,
        note_dt: datetime,
        ts: str,
    ) -> str:
        """Upload a voice message and return attachment relative path."""
        rel_path = await self._save_media(note_dt, ts, ".ogg")
        payload = await self._download_media_bytes(voice)
        await self._queue_put_content(
            self._repo_path(rel_path),
            payload,
            f"telejournal: add {rel_path}",
        )
        return rel_path

    async def save_video(
        self,
        video: Video,
        note_dt: datetime,
        ts: str,
    ) -> str:
        """Upload a video and return attachment relative path."""
        rel_path = await self._save_media(note_dt, ts, ".mp4")
        payload = await self._download_media_bytes(video)
        await self._queue_put_content(
            self._repo_path(rel_path),
            payload,
            f"telejournal: add {rel_path}",
        )
        return rel_path

    async def save_video_note(
        self,
        video_note: VideoNote,
        note_dt: datetime,
        ts: str,
    ) -> str:
        """Upload a video note and return attachment relative path."""
        rel_path = await self._save_media(note_dt, f"{ts}_note", ".mp4")
        payload = await self._download_media_bytes(video_note)
        await self._queue_put_content(
            self._repo_path(rel_path),
            payload,
            f"telejournal: add {rel_path}",
        )
        return rel_path

    async def get_attachment_bytes(self, attachment_rel: str) -> bytes | None:
        """Return raw attachment bytes for rendered output in Telegram."""
        repo_path = self._repo_path(attachment_rel)
        queued = await self._pending_write_for_path(repo_path)
        if queued is not None:
            return queued.payload

        if await self._is_pending_delete(repo_path):
            return None

        payload = await asyncio.to_thread(
            self._get_content,
            repo_path,
        )
        if payload is None:
            return None
        return self._decode_content(payload)
