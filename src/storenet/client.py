"""Synchronous StoreNet Partner API client for shop designs."""

from __future__ import annotations

import builtins
import http.client
import ipaddress
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

from storenet._signing import auth_headers, format_timestamp
from storenet._version import __version__
from storenet.errors import StorenetConfigError, StorenetError

API_PREFIX = "/api/public/v1"
ENV_BASE_URL = "STORENET_BASE_URL"
ENV_KEY_ID = "STORENET_KEY_ID"
ENV_SECRET = "STORENET_SECRET"
DEFAULT_TIMEOUT = 130.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_AFTER = 60.0
BACKOFF_BASE = 2.0
PAGE_SIZE = 100
RETRYABLE_CODES = frozenset({"503310", "503190", "409171", "timeout", "network_error"})
EDIT_CONFLICT = "409310"


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    # Following a redirect would resend the signed headers to a host the user never configured.
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _is_uuid(value: str) -> bool:
    if len(value) not in (32, 36):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url_problem(name: str, code: str) -> str:
    if code == "insecure_base_url":
        return f"{name} must use https; plain http is allowed only for a loopback IP address."
    return f"{name} must be a full URL, for example https://shop.example.com"


@dataclass(frozen=True)
class Site:
    """A shop design created through the Partner API."""

    id: str
    name: str
    industry: str
    url: str
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Edit:
    """The result of one AI edit; `outcome` is "applied", "refused" or "empty"."""

    id: str
    outcome: str
    changed: list[str]
    refused: list[str]
    unsupported: list[str]
    credits_used: float
    credits_available: float | None
    url: str

    @property
    def applied(self) -> bool:
        return self.outcome == "applied"


def _invalid(message: str, status: int = 200) -> StorenetError:
    return StorenetError("invalid_response", status, message)


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise _invalid(f"Response is missing the field '{key}'.")
    return value


def _number(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"Field '{key}' is not a number.")
    return float(value)


def _texts(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(f"Field '{key}' is not a list of strings.")
    return list(value)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    # Python 3.10 fromisoformat does not accept a trailing "Z".
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _snippet(raw: bytes) -> str:
    return raw[:200].decode("utf-8", "replace")


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def error_from_response(status: int, raw: bytes, headers: Any = None) -> StorenetError:
    """Map a non-2xx response to a StorenetError."""
    if 300 <= status < 400:
        location = headers.get("Location") if headers is not None else None
        return StorenetError(
            "redirected",
            status,
            f"The server redirected (HTTP {status}) to {location or 'another address'}. "
            f"Set {ENV_BASE_URL} to the shop's final https address and try again.",
            {"location": location},
        )
    retry_after = _retry_after(headers)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return StorenetError(
            "invalid_response",
            status,
            f"The server returned HTTP {status} with a body that is not JSON.",
            {"body": _snippet(raw)},
            retry_after=retry_after,
        )
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("code") is not None:
            details = error.get("details")
            return StorenetError(
                str(error["code"]),
                status,
                str(error.get("message") or f"HTTP {status}"),
                details if isinstance(details, dict) else {},
                retry_after=retry_after,
            )
        detail = body.get("detail")
        if status in (404, 405) and detail is not None:
            return StorenetError(
                "not_deployed",
                status,
                "The shop's server does not support this operation yet. "
                "Ask the shop administrator to update it.",
                {"detail": detail},
            )
        if status == 422 and isinstance(detail, list):
            return StorenetError(
                "validation_error",
                status,
                "The server rejected the request data.",
                {"errors": detail},
            )
    return StorenetError(
        "invalid_response",
        status,
        f"The server returned HTTP {status} with an unrecognised body.",
        {"body": _snippet(raw)},
        retry_after=retry_after,
    )


def _error_body(exc: urllib.error.HTTPError) -> bytes:
    try:
        return exc.read() or b""
    except (AttributeError, OSError, http.client.HTTPException):
        return b""


def _retryable(error: StorenetError) -> bool:
    return error.status == 429 or error.code in RETRYABLE_CODES


class Sites:
    """Shop design operations, reached as `client.sites`."""

    def __init__(self, client: StorenetClient) -> None:
        self._client = client

    def create(self, prompt: str, idempotency_key: str | None = None) -> Site:
        """Generate a new design from a description (about 15 s)."""
        return self._client._site(self._create_data(prompt, idempotency_key))

    def update(self, site_id: str, prompt: str, idempotency_key: str | None = None) -> Edit:
        """Ask the AI to change a design; a refused edit is returned, not raised."""
        return self._client._edit(self._update_data(site_id, prompt, idempotency_key))

    def list(self) -> builtins.list[Site]:
        """Every design of the key's owner, newest first, across all pages."""
        return [self._client._site(item) for item in self._list_data()]

    def _create_data(self, prompt: str, idempotency_key: str | None = None) -> dict[str, Any]:
        envelope = self._client._call(
            "POST", "/shop-designs", body={"prompt": prompt}, idempotency_key=idempotency_key
        )
        return _data_object(envelope)

    def _update_data(
        self, site_id: str, prompt: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        site_id = site_id.strip()
        if not _is_uuid(site_id):
            raise ValueError("site_id must be a full UUID")
        envelope = self._client._call(
            "POST",
            f"/shop-designs/{site_id}/edits",
            body={"prompt": prompt},
            idempotency_key=idempotency_key,
            retry_once=frozenset({EDIT_CONFLICT}),
        )
        return _data_object(envelope)

    def _list_data(self) -> builtins.list[dict[str, Any]]:
        items: builtins.list[dict[str, Any]] = []
        page = 1
        while True:
            envelope = self._client._call(
                "GET", "/shop-designs", query={"page": page, "per_page": PAGE_SIZE}
            )
            data = envelope.get("data")
            meta = envelope.get("meta")
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise _invalid("List response is missing 'data' or 'meta'.")
            if not all(isinstance(item, dict) for item in data):
                raise _invalid("List item is not an object.")
            items.extend(data)
            total_pages = meta.get("total_pages")
            if not data or not isinstance(total_pages, int) or page >= total_pages:
                return items
            page += 1


def _data_object(envelope: Mapping[str, Any]) -> dict[str, Any]:
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise _invalid("Response is missing 'data'.")
    return data


class StorenetClient:
    """Signs and sends Partner API calls; retries transient failures with the same key."""

    def __init__(
        self,
        base_url: str,
        key_id: str,
        secret: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        given = {"base_url": base_url, "key_id": key_id, "secret": secret}
        missing = [name for name, value in given.items() if not (value or "").strip()]
        if missing:
            raise StorenetConfigError("Missing configuration: " + ", ".join(missing), missing)
        parts = urlsplit(base_url.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            code = "missing_config"
            raise StorenetConfigError(_base_url_problem("base_url", code), ["base_url"], code)
        if parts.scheme == "http" and not _is_loopback(parts.hostname):
            code = "insecure_base_url"
            raise StorenetConfigError(_base_url_problem("base_url", code), ["base_url"], code)
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        root = parts.path.rstrip("/")
        if root.endswith(API_PREFIX):
            root = root[: -len(API_PREFIX)]
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._root = root
        self.base_url = self._origin + root
        self.key_id = key_id.strip()
        self._secret = secret.strip()
        self.timeout = float(timeout)
        self.max_retries = max_retries
        self.sites = Sites(self)
        self._urlopen: Callable[..., Any] = urllib.request.build_opener(_RefuseRedirect()).open
        self._sleep: Callable[[float], None] = time.sleep
        self._now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self._nonce: Callable[[], str] = lambda: str(uuid.uuid4())

    @classmethod
    def from_env(
        cls, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
    ) -> StorenetClient:
        """Build a client from STORENET_BASE_URL, STORENET_KEY_ID and STORENET_SECRET."""
        names = (ENV_BASE_URL, ENV_KEY_ID, ENV_SECRET)
        values = {name: os.environ.get(name, "").strip() for name in names}
        missing = [name for name in names if not values[name]]
        if missing:
            raise StorenetConfigError(
                "Missing environment variables: " + ", ".join(missing), missing
            )
        try:
            return cls(
                values[ENV_BASE_URL],
                values[ENV_KEY_ID],
                values[ENV_SECRET],
                timeout=timeout,
                max_retries=max_retries,
            )
        except StorenetConfigError as exc:
            message = _base_url_problem(ENV_BASE_URL, exc.code)
            raise StorenetConfigError(message, [ENV_BASE_URL], exc.code) from exc

    def __repr__(self) -> str:
        return f"StorenetClient(base_url={self.base_url!r}, key_id={self.key_id!r})"

    def _absolute(self, url: str) -> str:
        return self._origin + url if url.startswith("/") else url

    def _site(self, payload: Mapping[str, Any]) -> Site:
        return Site(
            id=_text(payload, "id"),
            name=_text(payload, "name"),
            industry=_text(payload, "industry"),
            url=self._absolute(_text(payload, "url")),
            updated_at=_parse_datetime(payload.get("updated_at")),
        )

    def _edit(self, payload: Mapping[str, Any]) -> Edit:
        return Edit(
            id=_text(payload, "id"),
            outcome=_text(payload, "outcome"),
            changed=_texts(payload, "changed"),
            refused=_texts(payload, "refused"),
            unsupported=_texts(payload, "unsupported"),
            credits_used=_number(payload, "credits_used") or 0.0,
            credits_available=_number(payload, "credits_available"),
            url=self._absolute(_text(payload, "url")),
        )

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        query: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        retry_once: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        raw_body = b""
        if body is not None:
            raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        key = None if method == "GET" else (idempotency_key or uuid.uuid4().hex)
        full_path = self._root + API_PREFIX + path
        url = self._origin + full_path + (f"?{urlencode(query)}" if query else "")
        retries_left = self.max_retries
        once = set(retry_once)
        step = 0
        while True:
            try:
                return self._send(method, full_path, url, raw_body, key)
            except StorenetError as exc:
                if exc.code in once:
                    once.discard(exc.code)
                    continue
                if retries_left <= 0 or not _retryable(exc):
                    if key is not None:
                        exc.details["idempotency_key"] = key
                    raise
                retries_left -= 1
                if exc.retry_after is not None:
                    delay = min(exc.retry_after, MAX_RETRY_AFTER)
                else:
                    delay = BACKOFF_BASE * (2**step)
                step += 1
                self._sleep(delay)

    def _send(
        self, method: str, path: str, url: str, raw_body: bytes, key: str | None
    ) -> dict[str, Any]:
        headers = auth_headers(
            key_id=self.key_id,
            secret=self._secret,
            method=method,
            path=path,
            raw_body=raw_body,
            timestamp=format_timestamp(self._now()),
            nonce=self._nonce(),
        )
        headers["Accept"] = "application/json"
        headers["User-Agent"] = f"storenet-python/{__version__}"
        if raw_body:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(url, data=raw_body or None, headers=headers, method=method)
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                status = int(response.status)
                raw = response.read()
                response_headers = getattr(response, "headers", None)
        except urllib.error.HTTPError as exc:
            raise error_from_response(exc.code, _error_body(exc), exc.headers) from None
        except TimeoutError:
            raise self._timeout() from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise self._timeout() from None
            raise StorenetError(
                "network_error", 0, f"Could not connect to {self._origin}: {exc.reason}"
            ) from None
        except (http.client.HTTPException, OSError) as exc:
            raise StorenetError(
                "network_error", 0, f"Connection to {self._origin} dropped: {exc!r}"
            ) from None
        if not 200 <= status < 300:
            raise error_from_response(status, raw, response_headers)
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise StorenetError(
                "invalid_response",
                status,
                "The server returned a body that is not JSON.",
                {"body": _snippet(raw)},
            ) from None
        if not isinstance(envelope, dict) or "data" not in envelope:
            raise _invalid("Response has no 'data'.", status)
        return envelope

    def _timeout(self) -> StorenetError:
        return StorenetError(
            "timeout", 0, f"No response from {self._origin} within {self.timeout:g} seconds."
        )
