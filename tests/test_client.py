from __future__ import annotations

import io
import itertools
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timezone
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from storenet import Edit, Site, StorenetClient, StorenetConfigError, StorenetError
from storenet._signing import sign

BASE = "https://shop.example.com"
KEY_ID = "pk_test_fake"
SECRET = "sk_test_fake_secret_value"
SITE_ID = "1b2c3d4e-0000-4000-8000-000000000001"
SITE = {"id": SITE_ID, "name": "Tiệm Bánh Kem", "industry": "bakery", "url": f"{BASE}/d/tok"}
EDIT_APPLIED = {
    "id": SITE_ID,
    "outcome": "applied",
    "changed": ["Màu — đổi bộ màu"],
    "refused": [],
    "unsupported": [],
    "credits_used": 1.5,
    "credits_available": 98.5,
    "url": f"{BASE}/d/tok",
}


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def ok(payload: object, status: int = 200) -> FakeResponse:
    return FakeResponse(status, json.dumps(payload).encode("utf-8"))


def http_error(
    status: int, body: object, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(BASE, status, "error", message, io.BytesIO(raw))


def coded(status: int, code: str, headers: dict[str, str] | None = None) -> Exception:
    body = {"error": {"code": code, "message": f"lỗi {code}", "details": {}}}
    return http_error(status, body, headers)


class FakeTransport:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def header(request: urllib.request.Request, name: str) -> str | None:
    return request.get_header(name.capitalize())


def make_client(
    *outcomes: object, **kwargs: Any
) -> tuple[StorenetClient, FakeTransport, list[float]]:
    client = StorenetClient(BASE, KEY_ID, SECRET, **kwargs)
    transport = FakeTransport(*outcomes)
    sleeps: list[float] = []
    counter = itertools.count(1)
    client._urlopen = transport
    client._sleep = sleeps.append
    client._now = lambda: datetime(2026, 11, 1, 8, 0, next(counter) % 60, tzinfo=timezone.utc)
    client._nonce = lambda: f"nonce-{next(counter)}"
    return client, transport, sleeps


def assert_signed(request: urllib.request.Request) -> None:
    path = urllib.parse.urlsplit(request.full_url).path
    expected = sign(
        secret=SECRET,
        method=request.get_method(),
        path=path,
        raw_body=request.data or b"",  # type: ignore[arg-type]
        timestamp=header(request, "X-Api-Timestamp") or "",
        nonce=header(request, "X-Api-Nonce") or "",
    )
    assert header(request, "X-Api-Signature") == expected
    assert header(request, "Authorization") == f"ApiKey {KEY_ID}"


def test_create_returns_site_and_signs_request() -> None:
    client, transport, _ = make_client(ok({"data": SITE}, status=201))
    site = client.sites.create("Tiệm bánh nhỏ, tông kem ấm")
    assert site == Site(id=SITE_ID, name="Tiệm Bánh Kem", industry="bakery", url=f"{BASE}/d/tok")
    (request,) = transport.requests
    assert request.get_method() == "POST"
    assert request.full_url == f"{BASE}/api/public/v1/shop-designs"
    assert json.loads(request.data) == {"prompt": "Tiệm bánh nhỏ, tông kem ấm"}  # type: ignore[arg-type]
    assert header(request, "Idempotency-Key")
    assert_signed(request)


def test_applied_update_returns_edit() -> None:
    client, transport, _ = make_client(ok({"data": EDIT_APPLIED}))
    edit = client.sites.update(SITE_ID, "Chuyển sang gam màu đỏ")
    assert edit.applied
    assert edit.credits_used == 1.5
    assert edit.credits_available == 98.5
    assert transport.requests[0].full_url == f"{BASE}/api/public/v1/shop-designs/{SITE_ID}/edits"
    assert_signed(transport.requests[0])


def test_refused_update_returns_edit() -> None:
    refused = {
        **EDIT_APPLIED,
        "outcome": "refused",
        "changed": [],
        "refused": ["Logo — đổi ảnh: không hỗ trợ"],
        "credits_used": 0,
        "credits_available": None,
    }
    client, _, _ = make_client(ok({"data": refused}))
    edit = client.sites.update(SITE_ID, "Đổi logo")
    assert isinstance(edit, Edit)
    assert not edit.applied
    assert edit.refused == ["Logo — đổi ảnh: không hỗ trợ"]
    assert edit.credits_used == 0
    assert edit.credits_available is None


def test_list_walks_pages() -> None:
    def page(items: list[dict[str, object]], number: int) -> FakeResponse:
        meta = {"page": number, "per_page": 100, "total": 5, "total_pages": 3, "has_more": None}
        return ok({"data": items, "meta": meta})

    rows = [
        {**SITE, "id": f"0000000{n}-0000-4000-8000-000000000000", "updated_at": stamp}
        for n, stamp in enumerate(
            ["2026-11-01T08:30:00Z", "2026-11-01T08:30:00.123456+00:00", None, "bad", ""]
        )
    ]
    client, transport, _ = make_client(page(rows[:2], 1), page(rows[2:4], 2), page(rows[4:], 3))
    sites = client.sites.list()
    assert [site.id for site in sites] == [row["id"] for row in rows]
    assert sites[0].updated_at == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    assert sites[1].updated_at is not None
    assert [site.updated_at for site in sites[2:]] == [None, None, None]
    queries = [urllib.parse.urlsplit(r.full_url).query for r in transport.requests]
    assert queries == ["page=1&per_page=100", "page=2&per_page=100", "page=3&per_page=100"]
    for request in transport.requests:
        assert request.get_method() == "GET"
        assert header(request, "Idempotency-Key") is None
        assert_signed(request)


def test_list_stops_on_empty_page() -> None:
    meta = {"page": 1, "per_page": 100, "total": 0, "total_pages": 0}
    client, transport, _ = make_client(ok({"data": [], "meta": meta}))
    assert client.sites.list() == []
    assert len(transport.requests) == 1


def test_error_envelope_raises_storenet_error() -> None:
    body = {"error": {"code": "403110", "message": "Thiếu scope", "details": {"scope": "x"}}}
    client, _, _ = make_client(http_error(403, body))
    with pytest.raises(StorenetError) as caught:
        client.sites.list()
    assert caught.value.code == "403110"
    assert caught.value.status == 403
    assert caught.value.message == "Thiếu scope"
    assert caught.value.details == {"scope": "x"}


@pytest.mark.parametrize(("status", "detail"), [(404, "Not Found"), (405, "Method Not Allowed")])
def test_plain_404_405_are_not_deployed(status: int, detail: str) -> None:
    client, _, _ = make_client(http_error(status, {"detail": detail}))
    with pytest.raises(StorenetError) as caught:
        client.sites.list()
    assert caught.value.code == "not_deployed"
    assert caught.value.status == status


def test_422_detail_is_validation_error() -> None:
    detail = [{"loc": ["body", "prompt"], "msg": "Field required", "type": "missing"}]
    client, _, _ = make_client(http_error(422, {"detail": detail}))
    with pytest.raises(StorenetError) as caught:
        client.sites.create("x")
    assert caught.value.code == "validation_error"
    assert caught.value.details["errors"] == detail


@pytest.mark.parametrize(
    "outcome",
    [
        http_error(502, b"<html>Bad Gateway</html>"),
        FakeResponse(200, b"not json"),
        ok(["no", "envelope"]),
        ok({"data": {"id": SITE_ID}}),
    ],
    ids=["502-html", "200-text", "200-no-envelope", "200-missing-fields"],
)
def test_non_json_is_invalid_response(outcome: object) -> None:
    client, _, _ = make_client(outcome)
    with pytest.raises(StorenetError) as caught:
        client.sites.create("x")
    assert caught.value.code == "invalid_response"


RETRYABLE = {
    "429": coded(429, "429100", {"Retry-After": "3"}),
    "503310": coded(503, "503310"),
    "503190": coded(503, "503190"),
    "409171": coded(409, "409171"),
    "timeout": TimeoutError("timed out"),
    "url-timeout": urllib.error.URLError(TimeoutError("timed out")),
    "network": urllib.error.URLError(ConnectionRefusedError("refused")),
    "reset": ConnectionResetError("reset"),
}


@pytest.mark.parametrize("failure", list(RETRYABLE.values()), ids=list(RETRYABLE))
def test_retries_same_key_new_nonce(failure: Exception) -> None:
    client, transport, sleeps = make_client(failure, ok({"data": SITE}, status=201))
    assert client.sites.create("x").id == SITE_ID
    first, second = transport.requests
    assert header(first, "Idempotency-Key") == header(second, "Idempotency-Key")
    assert header(first, "X-Api-Nonce") != header(second, "X-Api-Nonce")
    assert header(first, "X-Api-Timestamp") != header(second, "X-Api-Timestamp")
    assert header(first, "X-Api-Signature") != header(second, "X-Api-Signature")
    assert_signed(second)
    assert sleeps == ([3.0] if failure is RETRYABLE["429"] else [2.0])


def test_retry_after_capped_at_60() -> None:
    client, _, sleeps = make_client(
        coded(429, "429310", {"Retry-After": "120"}), ok({"data": SITE})
    )
    client.sites.create("x")
    assert sleeps == [60.0]


def test_429_without_header_waits_2_then_4_and_raises_last_error() -> None:
    client, transport, sleeps = make_client(
        coded(429, "429100"), coded(503, "503190"), coded(429, "429310")
    )
    with pytest.raises(StorenetError) as caught:
        client.sites.create("x")
    assert caught.value.code == "429310"
    assert sleeps == [2.0, 4.0]
    assert len(transport.requests) == 3
    assert len({header(r, "Idempotency-Key") for r in transport.requests}) == 1


def test_max_retries_zero_never_retries() -> None:
    client, transport, _ = make_client(coded(503, "503190"), max_retries=0)
    with pytest.raises(StorenetError):
        client.sites.create("x")
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "403080"), (400, "400371"), (409, "409170"), (404, "404311"), (409, "409310")],
)
def test_no_retry_on_403080_or_other_4xx(status: int, code: str) -> None:
    client, transport, sleeps = make_client(coded(status, code), ok({"data": SITE}))
    with pytest.raises(StorenetError) as caught:
        client.sites.create("x")
    assert caught.value.code == code
    assert len(transport.requests) == 1
    assert sleeps == []


def test_update_retries_409310_once_then_raises() -> None:
    client, transport, _ = make_client(
        coded(409, "409310"), coded(409, "409310"), ok({"data": EDIT_APPLIED})
    )
    with pytest.raises(StorenetError) as caught:
        client.sites.update(SITE_ID, "đỏ")
    assert caught.value.code == "409310"
    assert len(transport.requests) == 2
    assert header(transport.requests[0], "Idempotency-Key") == header(
        transport.requests[1], "Idempotency-Key"
    )


def test_update_409310_once_then_success() -> None:
    client, transport, _ = make_client(coded(409, "409310"), ok({"data": EDIT_APPLIED}))
    assert client.sites.update(SITE_ID, "đỏ").applied
    assert len(transport.requests) == 2


def test_generated_key_per_call() -> None:
    client, transport, _ = make_client(ok({"data": SITE}), ok({"data": SITE}))
    client.sites.create("x")
    client.sites.create("x")
    keys = [header(r, "Idempotency-Key") for r in transport.requests]
    assert all(keys)
    assert keys[0] != keys[1]


def test_given_key_reused_on_retry() -> None:
    client, transport, _ = make_client(
        coded(503, "503310"), coded(409, "409171"), ok({"data": EDIT_APPLIED})
    )
    client.sites.update(SITE_ID, "đỏ", idempotency_key="my-key-1")
    assert [header(r, "Idempotency-Key") for r in transport.requests] == ["my-key-1"] * 3


def test_repr_and_errors_hide_secret() -> None:
    failures: list[object] = [
        coded(403, "403080"),
        http_error(404, {"detail": "Not Found"}),
        http_error(502, b"oops"),
        TimeoutError(),
        urllib.error.URLError("down"),
    ]
    client, _, _ = make_client(*failures, max_retries=0)
    assert SECRET not in repr(client)
    assert KEY_ID in repr(client)
    for _ in failures:
        with pytest.raises(StorenetError) as caught:
            client.sites.create("x")
        error = caught.value
        assert SECRET not in f"{error} {error!r} {error.message} {error.details}"


def test_from_env_names_missing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORENET_BASE_URL", BASE)
    monkeypatch.delenv("STORENET_KEY_ID", raising=False)
    monkeypatch.setenv("STORENET_SECRET", "  ")
    with pytest.raises(StorenetConfigError) as caught:
        StorenetClient.from_env()
    assert caught.value.code == "missing_config"
    assert caught.value.details == {"missing": ["STORENET_KEY_ID", "STORENET_SECRET"]}


def test_from_env_rejects_url_without_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORENET_BASE_URL", "shop.example.com")
    monkeypatch.setenv("STORENET_KEY_ID", KEY_ID)
    monkeypatch.setenv("STORENET_SECRET", SECRET)
    with pytest.raises(StorenetConfigError) as caught:
        StorenetClient.from_env()
    assert caught.value.details == {"missing": ["STORENET_BASE_URL"]}
    assert SECRET not in str(caught.value)


def test_base_url_with_api_prefix_and_relative_link() -> None:
    client = StorenetClient(BASE + "/api/public/v1/", KEY_ID, SECRET)
    transport = FakeTransport(ok({"data": {**SITE, "url": "/d/tok"}}))
    client._urlopen = transport
    site = client.sites.create("x")
    assert transport.requests[0].full_url == f"{BASE}/api/public/v1/shop-designs"
    assert site.url == f"{BASE}/d/tok"


EXHAUSTING = {
    "503310": lambda: coded(503, "503310"),
    "503190": lambda: coded(503, "503190"),
    "409171": lambda: coded(409, "409171"),
    "timeout": lambda: TimeoutError("timed out"),
    "network": lambda: urllib.error.URLError(ConnectionRefusedError("refused")),
}


@pytest.mark.parametrize("name", list(EXHAUSTING))
def test_exhausted_post_carries_generated_key(name: str) -> None:
    client, transport, _ = make_client(*(EXHAUSTING[name]() for _ in range(3)))
    with pytest.raises(StorenetError) as caught:
        client.sites.create("x")
    sent = {header(r, "Idempotency-Key") for r in transport.requests}
    assert len(transport.requests) == 3
    assert sent == {caught.value.details["idempotency_key"]}


def test_post_error_carries_given_key_and_get_error_carries_none() -> None:
    client, _, _ = make_client(coded(403, "403080"), coded(503, "503190"), max_retries=0)
    with pytest.raises(StorenetError) as post:
        client.sites.update(SITE_ID, "đỏ", idempotency_key="my-key-2")
    assert post.value.details["idempotency_key"] == "my-key-2"
    with pytest.raises(StorenetError) as get:
        client.sites.list()
    assert "idempotency_key" not in get.value.details


@pytest.mark.parametrize(
    "site_id",
    ["1b2c3d4e", "not-a-uuid", f"{SITE_ID}/../x", f"{{{SITE_ID}}}", "", "   "],
)
def test_update_rejects_non_uuid_before_sending(site_id: str) -> None:
    client, transport, _ = make_client(ok({"data": EDIT_APPLIED}))
    with pytest.raises(ValueError):
        client.sites.update(site_id, "đỏ")
    assert transport.requests == []


def test_update_accepts_hex_uuid_without_dashes() -> None:
    client, transport, _ = make_client(ok({"data": EDIT_APPLIED}))
    client.sites.update(SITE_ID.replace("-", ""), "đỏ")
    assert transport.requests[0].full_url.endswith(f"/{SITE_ID.replace('-', '')}/edits")


@pytest.mark.parametrize(
    "base_url",
    ["http://shop.example.com", "http://10.0.0.5:8000", "http://localhost.example.com"],
)
def test_plain_http_outside_loopback_is_refused(base_url: str) -> None:
    with pytest.raises(StorenetConfigError) as caught:
        StorenetClient(base_url, KEY_ID, SECRET)
    assert caught.value.code == "insecure_base_url"
    assert caught.value.details == {"missing": ["base_url"]}


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8000", "http://[::1]:8000"])
def test_plain_http_on_loopback_ip_is_allowed(base_url: str) -> None:
    assert StorenetClient(base_url, KEY_ID, SECRET).base_url == base_url


def test_from_env_keeps_insecure_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORENET_BASE_URL", "http://shop.example.com")
    monkeypatch.setenv("STORENET_KEY_ID", KEY_ID)
    monkeypatch.setenv("STORENET_SECRET", SECRET)
    with pytest.raises(StorenetConfigError) as caught:
        StorenetClient.from_env()
    assert caught.value.code == "insecure_base_url"
    assert caught.value.details == {"missing": ["STORENET_BASE_URL"]}
    assert "STORENET_BASE_URL" in caught.value.message


@pytest.fixture
def redirecting() -> Iterator[tuple[str, list[dict[str, str]]]]:
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(dict(self.headers.items()))
            if self.path.startswith("/elsewhere"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data": [], "meta": {"total_pages": 1}}')
                return
            self.send_response(301)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    httpd.shutdown()
    httpd.server_close()


def test_redirect_is_not_followed(redirecting: tuple[str, list[dict[str, str]]]) -> None:
    url, seen = redirecting
    client = StorenetClient(url, KEY_ID, SECRET, max_retries=0)
    with pytest.raises(StorenetError) as caught:
        client.sites.list()
    assert caught.value.code == "redirected"
    assert caught.value.status == 301
    assert caught.value.details == {"location": "/elsewhere"}
    assert "STORENET_BASE_URL" in caught.value.message
    assert len(seen) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_3xx_maps_to_redirected(status: int) -> None:
    target = "https://other.example.com/x"
    failure = http_error(status, b"", {"Location": target})
    client, transport, sleeps = make_client(failure, ok({"data": SITE}))
    with pytest.raises(StorenetError) as caught:
        client.sites.create("x")
    assert caught.value.code == "redirected"
    assert caught.value.details["location"] == target
    assert len(transport.requests) == 1
    assert sleeps == []
