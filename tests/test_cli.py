from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from storenet import __version__
from storenet.cli import main

KEY_ID = "pk_test_fake"
SECRET = "sk_test_fake_cli_secret"
PUBLIC = "https://shop.example.com"
ID_A = "1b2c3d4e-0000-4000-8000-00000000000a"
ID_B = "1b2c3d4e-1111-4000-8000-00000000000b"
ID_C = "9f8e7d6c-2222-4000-8000-00000000000c"
SITE_A = {
    "id": ID_A,
    "name": "Tiệm Bánh Kem",
    "industry": "bakery",
    "updated_at": "2026-11-01T08:30:00Z",
    "url": f"{PUBLIC}/d/aaa",
}
SITE_B = {**SITE_A, "id": ID_B, "name": "Thú Cưng Xinh", "industry": "pets", "url": f"{PUBLIC}/d/b"}
SITE_C = {**SITE_A, "id": ID_C, "name": "Cà Phê Sáng", "industry": "cafe", "url": f"{PUBLIC}/d/c"}
EDIT = {
    "id": ID_C,
    "outcome": "applied",
    "changed": ["Màu — đổi bộ màu", "Tiêu đề — đổi chữ"],
    "refused": ["Logo — đổi ảnh: chưa hỗ trợ"],
    "unsupported": ["Thêm video nền"],
    "credits_used": 1.5,
    "credits_available": 98.25,
    "url": f"{PUBLIC}/d/c",
}


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes


Responder = Callable[[Recorded], tuple[int, object]]


def _expected_signature(method: str, path: str, body: bytes, timestamp: str, nonce: str) -> str:
    canonical = "\n".join(
        [method.upper(), path, hashlib.sha256(body).hexdigest(), timestamp, nonce]
    )
    digest = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class FakeServer:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Responder] = {}
        self.requests: list[Recorded] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.handle(self)

            def do_POST(self) -> None:
                server.handle(self)

            def log_message(self, *args: object) -> None:
                return None

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(handler.path)
        body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
        recorded = Recorded(
            handler.command,
            unquote(parts.path),
            parse_qs(parts.query),
            dict(handler.headers.items()),
            body,
        )
        self.requests.append(recorded)
        expected = _expected_signature(
            recorded.method,
            recorded.path,
            body,
            recorded.headers.get("X-Api-Timestamp", ""),
            recorded.headers.get("X-Api-Nonce", ""),
        )
        if (
            recorded.headers.get("Authorization") != f"ApiKey {KEY_ID}"
            or recorded.headers.get("X-Api-Signature") != expected
        ):
            status, payload = 401, {"error": {"code": "401063", "message": "Sai chữ ký"}}
        else:
            responder = self.routes.get((recorded.method, recorded.path))
            status, payload = (
                (404, {"detail": "Not Found"}) if responder is None else responder(recorded)
            )
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def paths(self) -> list[tuple[str, str]]:
        return [(r.method, r.path) for r in self.requests]


LIST = ("GET", "/api/public/v1/shop-designs")
CREATE = ("POST", "/api/public/v1/shop-designs")


def edits(site_id: str) -> tuple[str, str]:
    return ("POST", f"/api/public/v1/shop-designs/{site_id}/edits")


def listing(*sites: dict[str, object], per_page: int = 2) -> Responder:
    total_pages = (len(sites) + per_page - 1) // per_page

    def respond(request: Recorded) -> tuple[int, object]:
        page = int(request.query.get("page", ["1"])[0])
        chunk = list(sites[(page - 1) * per_page : page * per_page])
        meta = {"page": page, "per_page": per_page, "total": len(sites)}
        return 200, {"data": chunk, "meta": {**meta, "total_pages": total_pages}}

    return respond


def reply(status: int, payload: object) -> Responder:
    return lambda _request: (status, payload)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:
    fake = FakeServer()
    monkeypatch.setenv("STORENET_BASE_URL", fake.url)
    monkeypatch.setenv("STORENET_KEY_ID", KEY_ID)
    monkeypatch.setenv("STORENET_SECRET", SECRET)
    yield fake
    fake.httpd.shutdown()
    fake.httpd.server_close()


def test_create_prints_name_industry_id_link_exit_0(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.routes[CREATE] = reply(201, {"data": SITE_A})
    assert main(["site", "create", "Tiệm bánh nhỏ ở phố cổ"]) == 0
    out = capsys.readouterr().out
    assert "Đã tạo: Tiệm Bánh Kem (bakery)" in out
    assert f"ID: {ID_A}" in out
    assert f"Xem: {PUBLIC}/d/aaa" in out
    assert json.loads(server.requests[0].body) == {"prompt": "Tiệm bánh nhỏ ở phố cổ"}
    assert server.requests[0].headers.get("Idempotency-Key")


def test_create_from_file(
    server: FakeServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt_file = tmp_path / "mota.txt"
    prompt_file.write_text("Cửa hàng thú cưng\nnhiều màu\n", encoding="utf-8")
    server.routes[CREATE] = reply(201, {"data": SITE_B})
    assert main(["site", "create", "--file", str(prompt_file), "--idempotency-key", "k-1"]) == 0
    assert json.loads(server.requests[0].body) == {"prompt": "Cửa hàng thú cưng\nnhiều màu"}
    assert server.requests[0].headers.get("Idempotency-Key") == "k-1"
    assert "Đã tạo: Thú Cưng Xinh (pets)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [["site", "create"], ["site", "create", "x", "--file", "f.txt"], ["site", "create", "   "]],
    ids=["no-prompt", "both", "blank"],
)
def test_create_prompt_usage_errors_exit_2(server: FakeServer, argv: list[str]) -> None:
    assert main(argv) == 2
    assert server.requests == []


def test_json_prints_raw_payload(server: FakeServer, capsys: pytest.CaptureFixture[str]) -> None:
    server.routes[CREATE] = reply(201, {"data": SITE_A})
    assert main(["site", "create", "x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == SITE_A
    server.routes[LIST] = listing(SITE_A, SITE_B, SITE_C)
    assert main(["site", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [SITE_A, SITE_B, SITE_C]


@pytest.mark.parametrize("variable", ["STORENET_BASE_URL", "STORENET_KEY_ID", "STORENET_SECRET"])
def test_missing_env_exits_2_names_variable_sends_nothing(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    variable: str,
) -> None:
    monkeypatch.delenv(variable)
    for argv in (["site", "list"], ["site", "create", "x"], ["site", "update", ID_A, "đỏ"]):
        assert main(argv) == 2
        assert variable in capsys.readouterr().err
    assert server.requests == []


def test_update_applied_prints_changes_credits_link_exit_0(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.routes[edits(ID_C)] = reply(200, {"data": EDIT})
    assert main(["site", "update", ID_C, "Chuyển sang gam màu đỏ"]) == 0
    out = capsys.readouterr().out
    assert "Đã đổi:\n  - Màu — đổi bộ màu\n  - Tiêu đề — đổi chữ" in out
    assert "Không làm được:\n  - Logo — đổi ảnh: chưa hỗ trợ\n  - Thêm video nền" in out
    assert "Credit: −1.5 (còn 98.25)" in out
    assert f"Xem: {PUBLIC}/d/c" in out


def test_update_not_applied_prints_reasons_exit_1(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = {**EDIT, "outcome": "refused", "changed": [], "credits_used": 0}
    server.routes[edits(ID_C)] = reply(200, {"data": refused})
    assert main(["site", "update", ID_C, "Đổi logo"]) == 1
    out = capsys.readouterr().out
    assert "AI không đổi gì (refused)." in out
    assert "Logo — đổi ảnh: chưa hỗ trợ" in out
    assert "Đã đổi" not in out
    assert "Credit" not in out


def test_update_prefix_one_match(server: FakeServer, capsys: pytest.CaptureFixture[str]) -> None:
    server.routes[LIST] = listing(SITE_A, SITE_B, SITE_C)
    server.routes[edits(ID_C)] = reply(200, {"data": EDIT})
    assert main(["site", "update", ID_C[:8], "đỏ"]) == 0
    assert server.paths()[-1] == edits(ID_C)
    assert "Đã đổi:" in capsys.readouterr().out


def test_update_prefix_ambiguous_exits_2(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.routes[LIST] = listing(SITE_A, SITE_B, SITE_C)
    assert main(["site", "update", ID_A[:8], "đỏ"]) == 2
    err = capsys.readouterr().err
    assert ID_A in err
    assert ID_B in err
    assert all(path == LIST for path in server.paths())


def test_update_prefix_no_match_exits_1(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.routes[LIST] = listing(SITE_A)
    assert main(["site", "update", "deadbeef", "đỏ"]) == 1
    assert "deadbeef" in capsys.readouterr().err
    assert all(path == LIST for path in server.paths())


def test_full_uuid_skips_list(server: FakeServer) -> None:
    server.routes[edits(ID_C)] = reply(200, {"data": EDIT})
    assert main(["site", "update", ID_C, "đỏ"]) == 0
    assert server.paths() == [edits(ID_C)]


def test_list_table_all_pages(server: FakeServer, capsys: pytest.CaptureFixture[str]) -> None:
    server.routes[LIST] = listing(SITE_A, SITE_B, SITE_C, per_page=1)
    assert main(["site", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["ID", "TÊN", "NGÀNH", "CẬP", "NHẬT", "LINK"]
    when = datetime.fromisoformat("2026-11-01T08:30:00+00:00").astimezone()
    for line, site in zip(lines[1:4], (SITE_A, SITE_B, SITE_C), strict=True):
        assert line.startswith(str(site["id"])[:8] + "  ")
        assert str(site["id"]) not in line
        for value in (site["name"], site["industry"], site["url"]):
            assert str(value) in line
        assert when.strftime("%Y-%m-%d %H:%M") in line
    assert lines[4] == "Tổng: 3 mẫu"
    assert [r.query["page"] for r in server.requests] == [["1"], ["2"], ["3"]]


def test_list_empty(server: FakeServer, capsys: pytest.CaptureFixture[str]) -> None:
    server.routes[LIST] = listing()
    assert main(["site", "list"]) == 0
    assert "Chưa có mẫu thiết kế nào" in capsys.readouterr().out


def test_server_error_prints_code_exit_1(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    error = {"error": {"code": "403290", "message": "Cần gói Pro", "details": {}}}
    server.routes[LIST] = reply(403, error)
    assert main(["site", "list"]) == 1
    assert "Lỗi 403290: Cần gói Pro" in capsys.readouterr().err


def test_route_missing_is_not_deployed_exit_1(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["site", "update", ID_C, "đỏ"]) == 1
    assert "not_deployed" in capsys.readouterr().err


def test_wrong_secret_is_rejected_by_fake_server(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STORENET_SECRET", "sk_test_other")
    server.routes[LIST] = listing(SITE_A)
    assert main(["site", "list"]) == 1
    assert "401063" in capsys.readouterr().err


def test_secret_never_printed(server: FakeServer, capsys: pytest.CaptureFixture[str]) -> None:
    server.routes[CREATE] = reply(201, {"data": SITE_A})
    server.routes[LIST] = listing(SITE_A, SITE_B)
    server.routes[edits(ID_C)] = reply(200, {"data": EDIT})
    runs = [
        ["site", "create", "x"],
        ["site", "create", "x", "--json"],
        ["site", "list"],
        ["site", "list", "--json"],
        ["site", "update", ID_C, "đỏ"],
        ["site", "update", "1b2c3d4e", "đỏ"],
        ["site", "update", "00000000", "đỏ"],
        ["site", "update", "ffffffff-ffff-4fff-8fff-ffffffffffff", "đỏ"],
        ["site", "bogus"],
    ]
    for argv in runs:
        main(argv)
        captured = capsys.readouterr()
        assert SECRET not in captured.out + captured.err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"storenet {__version__}"
    assert __version__ == "0.1.0"


@pytest.mark.parametrize(("status", "code"), [(503, "503190"), (503, "503310"), (409, "409171")])
def test_exhausted_create_prints_key_to_rerun_with(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    code: str,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    server.routes[CREATE] = reply(status, {"error": {"code": code, "message": "bận"}})
    assert main(["site", "create", "x"]) == 1
    keys = {request.headers["Idempotency-Key"] for request in server.requests}
    assert len(server.requests) == 3
    assert len(keys) == 1
    err = capsys.readouterr().err
    assert f"Lỗi {code}" in err
    assert f"--idempotency-key {keys.pop()} để nhận kết quả, không trừ credit lần nữa." in err


def test_exhausted_update_hint_keeps_given_key(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    server.routes[edits(ID_C)] = reply(503, {"error": {"code": "503190", "message": "bận"}})
    assert main(["site", "update", ID_C, "đỏ", "--idempotency-key", "k-123"]) == 1
    assert "Chạy lại với --idempotency-key k-123" in capsys.readouterr().err


def test_final_error_prints_no_rerun_hint(
    server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.routes[CREATE] = reply(403, {"error": {"code": "403080", "message": "Hết credit"}})
    assert main(["site", "create", "x"]) == 1
    assert "--idempotency-key" not in capsys.readouterr().err


def test_plain_http_to_remote_host_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STORENET_BASE_URL", "http://shop.example.com")
    monkeypatch.setenv("STORENET_KEY_ID", KEY_ID)
    monkeypatch.setenv("STORENET_SECRET", SECRET)
    assert main(["site", "list"]) == 2
    assert "STORENET_BASE_URL phải dùng https" in capsys.readouterr().err
