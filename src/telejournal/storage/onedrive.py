"""OneDrive storage provider."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import yaml
from telegram import PhotoSize, Video, VideoNote, Voice

from telejournal.formatting import marker_end_comment, marker_start_comment

from .common import (
    FlushEvent,
    FlushEventPublisher,
    LOGGER,
    NoteData,
    PendingWrite,
    ProviderCapabilities,
    StorageAuthorizationRequiredError,
    WriteVisibility,
    _TIMESTAMP_RE,
)
from .github import GitHubRepository


class OneDriveAuthorizationRequiredError(StorageAuthorizationRequiredError):
    """Raised when OneDrive operations require interactive device authorization."""


class OneDriveRepository(FlushEventPublisher):  # pragma: no cover
    """Persist journal notes in OneDrive through Microsoft Graph."""

    _DEFAULT_SCOPE = "offline_access Files.ReadWrite"
    capabilities = ProviderCapabilities(write_visibility=WriteVisibility.BUFFERED)

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        root_path: str,
        api_base_url: str = "https://graph.microsoft.com/v1.0",
        batch_window_seconds: int = 60,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_expires_at_utc: str | None = None,
        config_path: Path | None = None,
    ) -> None:
        """Initialize a OneDrive-backed storage provider."""
        super().__init__()
        self._tenant_id = tenant_id.strip() or "common"
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._root_path = root_path.strip().strip("/")
        self._api_base_url = api_base_url.rstrip("/")
        self._batch_window_seconds = max(1, int(batch_window_seconds))
        self._access_token = (access_token or "").strip() or None
        self._refresh_token = (refresh_token or "").strip() or None
        self._token_expires_at_utc = (token_expires_at_utc or "").strip() or None
        self._config_path = config_path
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._queue_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._pending_puts: dict[str, PendingWrite] = {}
        self._pending_deletes: set[str] = set()
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_cycle = 0
        self._auth_lock = threading.Lock()
        self._device_code_payload: dict[str, Any] | None = None
        self._device_code_expires_at: datetime | None = None
        self._initialize_auth_state()

    @property
    def vault_root(self) -> Path:
        """Expose a placeholder local path for API compatibility."""
        return Path("/")

    def _device_code_endpoint(self) -> str:
        """Return OAuth device-code endpoint for the configured tenant."""
        return (
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/"
            "devicecode"
        )

    def _token_endpoint(self) -> str:
        """Return OAuth token endpoint for the configured tenant."""
        return f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"

    def _initialize_auth_state(self) -> None:
        """Prime auth state at startup without failing process initialization."""
        if self._token_is_valid():
            return

        if self._refresh_token is not None:
            try:
                self._refresh_access_token()
                return
            except RuntimeError:
                LOGGER.warning(
                    "OneDrive token refresh failed during startup; "
                    "falling back to device-code flow"
                )

        self._ensure_device_code_payload()

    def _parse_expiry(self) -> datetime | None:
        """Parse the cached expiry timestamp in strict UTC format."""
        raw_expiry = self._token_expires_at_utc
        if raw_expiry is None:
            return None
        try:
            return datetime.strptime(raw_expiry, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError:
            return None

    def _token_is_valid(self) -> bool:
        """Return whether the current access token is still valid."""
        if self._access_token is None:
            return False
        expiry = self._parse_expiry()
        if expiry is None:
            return True
        return datetime.now(UTC) < expiry

    def _persist_tokens_if_possible(self) -> None:
        """Persist refreshed OneDrive token values into YAML config when present."""
        if self._config_path is None:
            return

        config_path = self._config_path.expanduser().resolve()
        if not config_path.exists() or not config_path.is_file():
            return

        with config_path.open("r", encoding="utf-8") as file_handle:
            loaded = yaml.safe_load(file_handle) or {}

        if not isinstance(loaded, dict):
            return

        storage = loaded.get("storage")
        if not isinstance(storage, dict):
            storage = {}
            loaded["storage"] = storage

        onedrive = storage.get("onedrive")
        if not isinstance(onedrive, dict):
            onedrive = {}
            storage["onedrive"] = onedrive

        storage["provider"] = "onedrive"
        onedrive["tenant_id"] = self._tenant_id
        onedrive["client_id"] = self._client_id
        onedrive["client_secret"] = self._client_secret
        onedrive["root_path"] = self._root_path
        onedrive["api_base_url"] = self._api_base_url
        onedrive["batch_window_seconds"] = self._batch_window_seconds
        onedrive["access_token"] = self._access_token
        onedrive["refresh_token"] = self._refresh_token
        onedrive["token_expires_at_utc"] = self._token_expires_at_utc

        rendered = yaml.safe_dump(loaded, sort_keys=False)
        config_path.write_text(rendered, encoding="utf-8")

    def _apply_token_payload(self, payload: dict[str, Any]) -> None:
        """Apply OAuth token response payload into in-memory cache."""
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        expires_in_raw = str(payload.get("expires_in") or "").strip()

        if not access_token:
            raise RuntimeError("OneDrive token response did not include access_token")

        self._access_token = access_token
        if refresh_token:
            self._refresh_token = refresh_token

        expires_at: str | None = None
        if expires_in_raw:
            expires_in = int(expires_in_raw)
            expiry_dt = datetime.now(UTC)
            expiry_dt = expiry_dt.replace(microsecond=0)
            expiry_dt = expiry_dt.fromtimestamp(
                expiry_dt.timestamp() + max(0, expires_in - 30),
                tz=UTC,
            )
            expires_at = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._token_expires_at_utc = expires_at
        self._persist_tokens_if_possible()

    def _request_form_token(self, payload: dict[str, str]) -> dict[str, Any]:
        """Submit one OAuth form request and return parsed JSON payload."""
        encoded = urllib_parse.urlencode(payload).encode("utf-8")
        request = urllib_request.Request(
            url=self._token_endpoint(),
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "telejournal",
            },
        )
        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                loaded = json.loads(body)
            except json.JSONDecodeError as decode_exc:
                raise RuntimeError(
                    f"OneDrive token request failed: HTTP {exc.code} {body}"
                ) from decode_exc
            if isinstance(loaded, dict):
                return loaded
            raise RuntimeError(
                f"OneDrive token request failed: HTTP {exc.code} {body}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"OneDrive token request failed: {exc}") from exc

        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise RuntimeError("Unexpected OneDrive token payload")
        return loaded

    def _request_device_code(self) -> dict[str, Any]:
        """Start device code flow and return the service response payload."""
        encoded = urllib_parse.urlencode(
            {"client_id": self._client_id, "scope": self._DEFAULT_SCOPE}
        ).encode("utf-8")
        request = urllib_request.Request(
            url=self._device_code_endpoint(),
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "telejournal",
            },
        )
        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OneDrive device-code request failed: HTTP {exc.code} {body}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"OneDrive device-code request failed: {exc}") from exc

        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise RuntimeError("Unexpected OneDrive device-code payload")
        return loaded

    def _refresh_access_token(self) -> None:
        """Refresh access token using cached refresh token."""
        refresh_token = self._refresh_token
        if refresh_token is None:
            raise RuntimeError("No OneDrive refresh token available")

        payload = self._request_form_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": self._DEFAULT_SCOPE,
            }
        )
        error_code = str(payload.get("error") or "").strip()
        if error_code:
            description = str(payload.get("error_description") or "").strip()
            raise RuntimeError(
                f"OneDrive refresh token request failed: {error_code} {description}"
            )
        self._apply_token_payload(payload)

    def _build_device_code_instructions(self, device_payload: dict[str, Any]) -> str:
        """Render operator-facing instructions from a device-code response."""
        message = str(device_payload.get("message") or "").strip()
        verification_uri = str(device_payload.get("verification_uri") or "").strip()
        verification_uri_complete = str(
            device_payload.get("verification_uri_complete") or ""
        ).strip()
        user_code = str(device_payload.get("user_code") or "").strip()

        details: list[str] = [
            "OneDrive authorization is required.",
        ]
        if message:
            details.append(message)
        else:
            details.append(
                (
                    "Open the verification URL, complete login, then run "
                    "/storageauth complete to capture tokens."
                )
            )
        if verification_uri_complete:
            details.append(f"Verification URL: {verification_uri_complete}")
        if verification_uri and user_code:
            details.append(f"Verification URL: {verification_uri}")
            details.append(f"User code: {user_code}")
        details.append("Use /storageauth start to restart authorization.")

        return "\n".join(details)

    def _device_code_is_expired(self) -> bool:
        """Return whether cached device code payload is absent or expired."""
        if self._device_code_payload is None or self._device_code_expires_at is None:
            return True
        return datetime.now(UTC) >= self._device_code_expires_at

    def _ensure_device_code_payload(self, *, force_new: bool = False) -> dict[str, Any]:
        """Return a valid device-code payload, refreshing it when required."""
        if not force_new and not self._device_code_is_expired():
            assert self._device_code_payload is not None
            return self._device_code_payload

        payload = self._request_device_code()
        expires_in = int(str(payload.get("expires_in") or "900"))
        safe_expiry = datetime.now(UTC) + timedelta(seconds=max(30, expires_in - 30))
        self._device_code_payload = payload
        self._device_code_expires_at = safe_expiry
        return payload

    def start_device_authorization(self) -> str:
        """Force creation of a fresh device authorization challenge."""
        with self._auth_lock:
            payload = self._ensure_device_code_payload(force_new=True)
            return self._build_device_code_instructions(payload)

    def build_authorization_instructions(self) -> str | None:
        """Return current authorization instructions when interactive auth is needed."""
        if self._token_is_valid():
            return None

        with self._auth_lock:
            payload = self._ensure_device_code_payload()
            return self._build_device_code_instructions(payload)

    def complete_device_authorization(self) -> str:
        """Poll token endpoint once to complete active device authorization flow."""
        with self._auth_lock:
            payload = self._ensure_device_code_payload()
            device_code = str(payload.get("device_code") or "").strip()
            if not device_code:
                raise RuntimeError(
                    "OneDrive device code payload is missing device_code"
                )

            token_payload = self._request_form_token(
                {
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                }
            )

            error_code = str(token_payload.get("error") or "").strip()
            if error_code:
                if error_code in {"authorization_pending", "slow_down"}:
                    return (
                        "Authorization is still pending. Complete the login in the "
                        "browser and run /storageauth complete again."
                    )
                if error_code in {
                    "authorization_declined",
                    "bad_verification_code",
                    "expired_token",
                }:
                    self._device_code_payload = None
                    self._device_code_expires_at = None
                    return (
                        "Device authorization expired or was declined. "
                        "Run /storageauth start to generate a new code."
                    )
                description = str(token_payload.get("error_description") or "").strip()
                raise RuntimeError(
                    "OneDrive device authorization failed: "
                    f"{error_code} {description}"
                )

            self._apply_token_payload(token_payload)
            self._device_code_payload = None
            self._device_code_expires_at = None
            return "OneDrive authorization completed successfully."

    def is_authorized(self) -> bool:
        """Return whether an access token is currently usable."""
        return self._token_is_valid()

    def _ensure_auth_for_request(self) -> None:
        """Ensure a valid access token exists before one Graph API call."""
        if self._token_is_valid():
            return

        with self._auth_lock:
            if self._token_is_valid():
                return
            if self._refresh_token is not None:
                try:
                    self._refresh_access_token()
                    return
                except RuntimeError:
                    LOGGER.warning(
                        "OneDrive refresh failed; interactive authorization is required"
                    )

            payload = self._ensure_device_code_payload()
            instructions = self._build_device_code_instructions(payload)
            raise OneDriveAuthorizationRequiredError(instructions)

    def _build_headers(self, *, with_json: bool = True) -> dict[str, str]:
        """Build Graph request headers with bearer authentication."""
        self._ensure_auth_for_request()
        token = self._access_token
        if token is None:
            raise RuntimeError("OneDrive authentication token is unavailable")
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "telejournal",
        }
        if with_json:
            headers["Accept"] = "application/json"
        return headers

    @staticmethod
    def _read_http_error_body(exc: urllib_error.HTTPError) -> str:
        """Read an HTTP error response body as text for diagnostics."""
        try:
            body_bytes = exc.read()
        except OSError:
            return ""
        return body_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def _redact_url_for_logs(raw_url: str) -> str:
        """Redact query/fragment from URLs before logging diagnostics."""
        parsed = urllib_parse.urlsplit(raw_url)
        safe_parts = (parsed.scheme, parsed.netloc, parsed.path, "", "")
        return urllib_parse.urlunsplit(safe_parts)

    @staticmethod
    def _format_graph_error_details(body: str) -> str:
        """Extract concise error information from Graph-style JSON payloads."""
        if not body.strip():
            return ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return ""

        if not isinstance(payload, dict):
            return ""

        error_node = payload.get("error")
        if isinstance(error_node, dict):
            code = str(error_node.get("code") or "").strip()
            message = str(error_node.get("message") or "").strip()
        else:
            code = str(payload.get("error") or "").strip()
            message = str(payload.get("error_description") or "").strip()

        parts = [part for part in (code, message) if part]
        if not parts:
            return ""
        return " - " + ": ".join(parts)

    def _log_graph_http_error(
        self,
        *,
        method: str,
        endpoint: str,
        exc: urllib_error.HTTPError,
        body: str,
    ) -> None:
        """Log structured Graph error diagnostics to aid auth troubleshooting."""
        headers = exc.headers
        request_id = ""
        client_request_id = ""
        www_authenticate = ""
        location = ""
        if headers is not None:
            request_id = str(headers.get("request-id") or "").strip()
            client_request_id = str(headers.get("client-request-id") or "").strip()
            www_authenticate = str(headers.get("WWW-Authenticate") or "").strip()
            location = str(headers.get("Location") or "").strip()

        details = self._format_graph_error_details(body)
        safe_location = self._redact_url_for_logs(location) if location else ""
        LOGGER.warning(
            (
                "OneDrive HTTP error: method=%s endpoint=%s status=%s"
                " request-id=%s client-request-id=%s location=%s"
                " www-authenticate=%s graph-error=%s"
            ),
            method,
            endpoint,
            exc.code,
            request_id or "-",
            client_request_id or "-",
            safe_location or "-",
            www_authenticate or "-",
            details[3:] if details else "-",
        )

    def _try_refresh_after_unauthorized(self) -> bool:
        """Attempt one token refresh after a 401 response."""
        if self._refresh_token is None:
            return False

        with self._auth_lock:
            if self._refresh_token is None:
                return False
            self._access_token = None
            self._token_expires_at_utc = None
            try:
                self._refresh_access_token()
            except RuntimeError:
                LOGGER.warning(
                    (
                        "OneDrive token refresh after HTTP 401 failed; "
                        "interactive re-authorization is required"
                    )
                )
                return False
        return True

    def _authorization_required_error(self) -> OneDriveAuthorizationRequiredError:
        """Build a consistent interactive-authorization error for callers."""
        self._access_token = None
        self._token_expires_at_utc = None
        instructions = self.build_authorization_instructions()
        if instructions is None:
            instructions = (
                "OneDrive authorization is required. "
                "Use /storageauth [start|complete|status]."
            )
        return OneDriveAuthorizationRequiredError(instructions)

    class _NoFollowRedirect(urllib_request.HTTPRedirectHandler):
        """Redirect handler that surfaces 30x responses instead of following them."""

        def redirect_request(
            self,
            req: urllib_request.Request,
            fp: Any,
            code: int,
            msg: str,
            headers: Any,
            newurl: str,
        ) -> urllib_request.Request | None:
            del req, fp, code, msg, headers, newurl
            return None

    def _request_content_via_redirect(self, endpoint: str) -> bytes | None:
        """Fetch file content by resolving Graph /content redirect explicitly."""
        url = f"{self._api_base_url}{endpoint}"
        redirect_codes = {301, 302, 303, 307, 308}
        for attempt in range(2):
            headers = self._build_headers(with_json=False)
            request = urllib_request.Request(
                url=url,
                method="GET",
                headers=headers,
            )
            opener = urllib_request.build_opener(self._NoFollowRedirect())
            try:
                with opener.open(request, timeout=30) as response:
                    location = str(response.headers.get("Location") or "").strip()
                    if location:
                        return self._request_download_bytes(location)
                    return bytes(response.read())
            except urllib_error.HTTPError as exc:
                error_body = self._read_http_error_body(exc)
                if exc.code in redirect_codes:
                    location = str(exc.headers.get("Location") or "").strip()
                    if location:
                        return self._request_download_bytes(location)
                self._log_graph_http_error(
                    method="GET",
                    endpoint=endpoint,
                    exc=exc,
                    body=error_body,
                )
                if exc.code == 404:
                    return None
                if exc.code == 401:
                    if attempt == 0 and self._try_refresh_after_unauthorized():
                        continue
                    raise self._authorization_required_error() from exc
                details = self._format_graph_error_details(error_body)
                raise RuntimeError(
                    (
                        "OneDrive API request failed "
                        f"(GET {endpoint}): {exc.code}{details}"
                    )
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"OneDrive API request failed (GET {endpoint}): {exc}"
                ) from exc

        raise self._authorization_required_error()

    def _request_json(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any | None:
        """Execute one Graph API JSON request and decode the response."""
        url = f"{self._api_base_url}{endpoint}"
        body: bytes | None = None
        for attempt in range(2):
            headers = self._build_headers(with_json=True)
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"

            request = urllib_request.Request(
                url=url,
                data=body,
                method=method,
                headers=headers,
            )

            try:
                with urllib_request.urlopen(request, timeout=30) as response:
                    raw = response.read().decode("utf-8")
                break
            except urllib_error.HTTPError as exc:
                error_body = self._read_http_error_body(exc)
                if allow_not_found and exc.code == 404:
                    LOGGER.debug(
                        (
                            "OneDrive optional lookup returned 404 "
                            "(method=%s endpoint=%s)"
                        ),
                        method,
                        endpoint,
                    )
                    return None
                self._log_graph_http_error(
                    method=method,
                    endpoint=endpoint,
                    exc=exc,
                    body=error_body,
                )
                if exc.code == 401:
                    if attempt == 0 and self._try_refresh_after_unauthorized():
                        continue
                    raise self._authorization_required_error() from exc
                details = self._format_graph_error_details(error_body)
                raise RuntimeError(
                    (
                        "OneDrive API request failed "
                        f"({method} {endpoint}): {exc.code}{details}"
                    )
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"OneDrive API request failed ({method} {endpoint}): {exc}"
                ) from exc

        if not raw.strip():
            return None
        return json.loads(raw)

    def _request_bytes(
        self,
        method: str,
        endpoint: str,
        payload: bytes | None = None,
        *,
        allow_not_found: bool = False,
    ) -> bytes | None:
        """Execute one Graph API byte request and return raw response bytes."""
        url = f"{self._api_base_url}{endpoint}"
        for attempt in range(2):
            headers = self._build_headers(with_json=False)
            if payload is not None:
                headers["Content-Type"] = "application/octet-stream"

            request = urllib_request.Request(
                url=url,
                data=payload,
                method=method,
                headers=headers,
            )

            try:
                with urllib_request.urlopen(request, timeout=30) as response:
                    return bytes(response.read())
            except urllib_error.HTTPError as exc:
                error_body = self._read_http_error_body(exc)
                if allow_not_found and exc.code == 404:
                    LOGGER.debug(
                        (
                            "OneDrive optional lookup returned 404 "
                            "(method=%s endpoint=%s)"
                        ),
                        method,
                        endpoint,
                    )
                    return None
                self._log_graph_http_error(
                    method=method,
                    endpoint=endpoint,
                    exc=exc,
                    body=error_body,
                )
                if exc.code == 401:
                    if attempt == 0 and self._try_refresh_after_unauthorized():
                        continue
                    raise self._authorization_required_error() from exc
                details = self._format_graph_error_details(error_body)
                raise RuntimeError(
                    (
                        "OneDrive API request failed "
                        f"({method} {endpoint}): {exc.code}{details}"
                    )
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"OneDrive API request failed ({method} {endpoint}): {exc}"
                ) from exc

        raise RuntimeError("OneDrive API request failed after retry")

    def _request_download_bytes(self, download_url: str) -> bytes:
        """Fetch file bytes directly from a preauthenticated download URL."""
        request = urllib_request.Request(
            url=download_url,
            method="GET",
            headers={"User-Agent": "telejournal"},
        )
        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                return bytes(response.read())
        except urllib_error.HTTPError as exc:
            body = self._read_http_error_body(exc)
            safe_url = self._redact_url_for_logs(download_url)
            details = self._format_graph_error_details(body)
            raise RuntimeError(
                (
                    "OneDrive download URL request failed "
                    f"({safe_url}): {exc.code}{details}"
                )
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"OneDrive download URL request failed ({exc})") from exc

    def _path_endpoint(self, repo_path: str, *, content: bool = False) -> str:
        """Build Graph endpoint for one drive item path."""
        normalized = repo_path.strip("/")
        if not normalized:
            return "/me/drive/root"
        quoted = urllib_parse.quote(normalized, safe="/")
        suffix = "/content" if content else ""
        return f"/me/drive/root:/{quoted}:{suffix}"

    def _children_endpoint(self, repo_path: str) -> str:
        """Build Graph endpoint for listing one folder's children."""
        normalized = repo_path.strip("/")
        if not normalized:
            return "/me/drive/root/children"
        quoted = urllib_parse.quote(normalized, safe="/")
        return f"/me/drive/root:/{quoted}:/children"

    def _repo_path(self, rel_path: str) -> str:
        """Return OneDrive path under configured root folder."""
        normalized = rel_path.replace("\\", "/").strip("/")
        if not self._root_path:
            return normalized
        if not normalized:
            return self._root_path
        return f"{self._root_path}/{normalized}"

    def _note_relpath(self, note_dt: datetime) -> str:
        """Return note path relative to provider root."""
        return f"{note_dt.year}/{note_dt.strftime('%Y-%m-%d')}.md"

    @staticmethod
    def _default_frontmatter(note_dt: datetime) -> dict[str, Any]:
        """Create default YAML frontmatter for a date."""
        return GitHubRepository._default_frontmatter(note_dt)

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """Extract YAML frontmatter and markdown body from note content."""
        return GitHubRepository._split_frontmatter(content)

    @staticmethod
    def _serialize_note(frontmatter: dict[str, Any], body: str) -> str:
        """Serialize frontmatter and body into markdown file content."""
        return GitHubRepository._serialize_note(frontmatter, body)

    def get_note_path(self, note_dt: datetime) -> Path:
        """Return note-like path object for compatibility with call sites."""
        return Path(self._repo_path(self._note_relpath(note_dt)))

    def _get_item(self, repo_path: str) -> dict[str, Any] | None:
        """Fetch OneDrive item metadata for one path."""
        payload = self._request_json(
            "GET",
            self._path_endpoint(repo_path),
            allow_not_found=True,
        )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected OneDrive metadata payload")
        return payload

    def _ensure_folder(self, repo_path: str) -> None:
        """Ensure a folder exists in OneDrive, creating it when absent."""
        if not repo_path:
            return
        existing = self._get_item(repo_path)
        if existing is not None:
            if isinstance(existing.get("folder"), dict):
                return
            raise RuntimeError(f"OneDrive path is not a folder: {repo_path}")

        parent = "/".join(repo_path.split("/")[:-1]).strip("/")
        name = repo_path.split("/")[-1]
        endpoint = self._children_endpoint(parent)
        payload = {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "replace",
        }
        self._request_json("POST", endpoint, payload)

    def _ensure_parent_folders(self, repo_path: str) -> None:
        """Ensure all parent folders for one path exist remotely."""
        normalized = repo_path.strip("/")
        if "/" not in normalized:
            return

        current = ""
        parent_parts = normalized.split("/")[:-1]
        for part in parent_parts:
            current = f"{current}/{part}".strip("/")
            self._ensure_folder(current)

    def _get_content(self, repo_path: str) -> bytes | None:
        """Return one remote file payload as bytes when present."""
        metadata = self._request_json(
            "GET",
            (
                f"{self._path_endpoint(repo_path)}"
                "?$select=id,eTag,@microsoft.graph.downloadUrl"
            ),
            allow_not_found=True,
        )
        if metadata is None:
            return None
        if not isinstance(metadata, dict):
            raise RuntimeError("Unexpected OneDrive metadata payload")

        download_url = str(metadata.get("@microsoft.graph.downloadUrl") or "").strip()
        if download_url:
            return self._request_download_bytes(download_url)

        # Avoid forwarding Graph bearer tokens across redirected download hosts.
        return self._request_content_via_redirect(
            self._path_endpoint(repo_path, content=True)
        )

    def _put_content(self, repo_path: str, payload: bytes) -> None:
        """Create or update one remote file payload."""
        self._ensure_parent_folders(repo_path)
        self._request_bytes(
            "PUT",
            self._path_endpoint(repo_path, content=True),
            payload=payload,
        )

    def _delete_content(self, repo_path: str) -> bool:
        """Delete one remote file by path."""
        response = self._request_json(
            "DELETE",
            self._path_endpoint(repo_path),
            allow_not_found=True,
        )
        return response is not None or self._get_item(repo_path) is None

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
            name="telejournal-onedrive-flush",
        )
        LOGGER.info(
            "Started OneDrive batch flush worker (window=%ss)",
            self._batch_window_seconds,
        )

    async def _flush_loop(self) -> None:
        """Flush queued writes at a fixed interval in the background."""
        while True:
            try:
                await asyncio.sleep(self._batch_window_seconds)
                await self.flush_pending(reason="timer")
            except asyncio.CancelledError:
                LOGGER.info("Stopped OneDrive batch flush worker")
                raise
            except Exception:
                LOGGER.exception("Unexpected failure in OneDrive batch flush worker")

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
            "Queued OneDrive upsert for %s (pending items=%d)",
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
            "Queued OneDrive delete for %s (pending items=%d)",
            repo_path,
            pending_total,
        )

    def _flush_put_content(self, repo_path: str, pending: PendingWrite) -> None:
        """Flush one queued write to OneDrive."""
        self._put_content(repo_path, pending.payload)

    def _flush_delete_content(self, repo_path: str) -> None:
        """Flush one queued delete, skipping when path does not exist remotely."""
        if self._get_item(repo_path) is None:
            return
        self._delete_content(repo_path)

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
                    "OneDrive batch flush skipped (empty queue, reason=%s)",
                    reason,
                )
                return

            self._flush_cycle += 1
            LOGGER.info(
                ("Flushing OneDrive batch #%d " "(%d upserts, %d deletes; reason=%s)"),
                self._flush_cycle,
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
                    LOGGER.exception("OneDrive batch delete failed for %s", repo_path)
                    failed_deletes.add(repo_path)

            for repo_path, pending in pending_puts.items():
                try:
                    await asyncio.to_thread(self._flush_put_content, repo_path, pending)
                except Exception:
                    LOGGER.exception("OneDrive batch upsert failed for %s", repo_path)
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
                        "OneDrive batch #%d completed with %d failed upserts "
                        "and %d failed deletes; re-queued pending items=%d"
                    ),
                    self._flush_cycle,
                    len(failed_puts),
                    len(failed_deletes),
                    remaining,
                )
                return

            LOGGER.info(
                "OneDrive batch #%d flush completed successfully", self._flush_cycle
            )
            self._emit_flush_event(
                FlushEvent(
                    provider="onedrive",
                    flush_cycle=self._flush_cycle,
                    upserts=len(pending_puts),
                    deletes=len(pending_deletes),
                    reason=reason,
                )
            )

    async def _read_note(self, note_path: str) -> tuple[NoteData, str | None]:
        """Read note content from OneDrive and return parsed note + eTag."""
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
            decoded = payload.decode("utf-8")
            frontmatter, body = self._split_frontmatter(decoded)
            item = self._get_item(note_path)
            etag = None
            if item is not None:
                etag = str(item.get("eTag") or "") or None
            return NoteData(frontmatter=frontmatter, body=body), etag

        return await asyncio.to_thread(_read_sync)

    async def _write_note(
        self,
        note_path: str,
        frontmatter: dict[str, Any],
        body: str,
        sha: str | None,
    ) -> None:
        """Queue note content for batched OneDrive create-or-update operations."""
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

    def _list_children(self, repo_path: str) -> list[dict[str, Any]]:
        """List child items for one OneDrive folder path."""
        payload = self._request_json(
            "GET",
            self._children_endpoint(repo_path),
            allow_not_found=True,
        )
        if payload is None:
            return []
        if not isinstance(payload, dict):
            return []
        values = payload.get("value")
        if not isinstance(values, list):
            return []
        results: list[dict[str, Any]] = []
        for entry in values:
            if isinstance(entry, dict):
                results.append(entry)
        return results

    async def get_same_day_previous_year_notes(
        self,
        reference_dt: datetime,
    ) -> list[tuple[datetime, str]]:
        """Return same-day notes from all previous years in OneDrive."""

        def _collect_years() -> list[int]:
            root_path = self._repo_path("")
            entries = self._list_children(root_path)
            years: list[int] = []
            for entry in entries:
                if not isinstance(entry.get("folder"), dict):
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

            payload = await asyncio.to_thread(self._get_item, note_path)
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
        """Reserve the next available attachment filename in OneDrive tree."""
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

            payload = await asyncio.to_thread(self._get_item, repo_path)
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

        return await asyncio.to_thread(self._get_content, repo_path)
