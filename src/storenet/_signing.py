"""HMAC-SHA256 request signing for the StoreNet Partner API."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote


def _signed_path(path: str) -> str:
    # The server signs the decoded path without the query string.
    return unquote(path.split("?", 1)[0])


def format_timestamp(moment: datetime) -> str:
    """ISO-8601 UTC with a trailing Z, the form the documented vector uses."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign(
    *, secret: str, method: str, path: str, raw_body: bytes, timestamp: str, nonce: str
) -> str:
    """Return `sha256=<hex>` over METHOD, path, body sha256, timestamp and nonce."""
    body_hash = hashlib.sha256(raw_body).hexdigest()
    canonical = "\n".join([method.upper(), _signed_path(path), body_hash, timestamp, nonce])
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


def auth_headers(
    *,
    key_id: str,
    secret: str,
    method: str,
    path: str,
    raw_body: bytes,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Authorization, timestamp, nonce and signature headers for one request."""
    timestamp = timestamp or format_timestamp(datetime.now(timezone.utc))
    nonce = nonce or str(uuid.uuid4())
    return {
        "Authorization": f"ApiKey {key_id}",
        "X-Api-Timestamp": timestamp,
        "X-Api-Nonce": nonce,
        "X-Api-Signature": sign(
            secret=secret,
            method=method,
            path=path,
            raw_body=raw_body,
            timestamp=timestamp,
            nonce=nonce,
        ),
    }
