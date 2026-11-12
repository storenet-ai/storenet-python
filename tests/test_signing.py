from __future__ import annotations

from storenet._signing import auth_headers, sign

VECTOR_SECRET = "sk_test_examplesecret_do_not_use"
VECTOR_PATH = "/api/public/v1/products"
VECTOR_BODY = (
    b'{"external_ref":"SKU-1001","product_type_code":"book","title":"Dac Nhan Tam",'
    b'"sale_price_vnd":86000,"original_price_vnd":108000}'
)
VECTOR_TIMESTAMP = "2026-08-20T07:30:00Z"
VECTOR_NONCE = "3f2b1c9e-8a4d-4c6f-9e2a-1b5d7c8e9f01"
VECTOR_SIGNATURE = "sha256=7a76cfd62cffa3962e3d7bd4268d73cb13c5c334a9acc4e52649d3fd57585465"


def _vector(**overrides: object) -> str:
    params: dict[str, object] = {
        "secret": VECTOR_SECRET,
        "method": "POST",
        "path": VECTOR_PATH,
        "raw_body": VECTOR_BODY,
        "timestamp": VECTOR_TIMESTAMP,
        "nonce": VECTOR_NONCE,
    }
    params.update(overrides)
    return sign(**params)  # type: ignore[arg-type]


def test_documented_vector() -> None:
    assert _vector() == VECTOR_SIGNATURE


def test_method_uppercased() -> None:
    assert _vector(method="post") == VECTOR_SIGNATURE


def test_query_not_signed() -> None:
    assert _vector(path=VECTOR_PATH + "?page=2&per_page=100") == VECTOR_SIGNATURE


def test_encoded_path_is_signed_decoded() -> None:
    encoded = sign(secret="s", method="GET", path="/a%3Ab", raw_body=b"", timestamp="t", nonce="n")
    decoded = sign(secret="s", method="GET", path="/a:b", raw_body=b"", timestamp="t", nonce="n")
    assert encoded == decoded


def test_auth_headers_carry_key_and_signature_but_not_secret() -> None:
    headers = auth_headers(
        key_id="pk_test_fake",
        secret=VECTOR_SECRET,
        method="POST",
        path=VECTOR_PATH,
        raw_body=VECTOR_BODY,
        timestamp=VECTOR_TIMESTAMP,
        nonce=VECTOR_NONCE,
    )
    assert headers == {
        "Authorization": "ApiKey pk_test_fake",
        "X-Api-Timestamp": VECTOR_TIMESTAMP,
        "X-Api-Nonce": VECTOR_NONCE,
        "X-Api-Signature": VECTOR_SIGNATURE,
    }
    assert all(VECTOR_SECRET not in value for value in headers.values())


def test_auth_headers_generate_fresh_nonce_and_timestamp() -> None:
    first = auth_headers(key_id="k", secret="s", method="GET", path="/", raw_body=b"")
    second = auth_headers(key_id="k", secret="s", method="GET", path="/", raw_body=b"")
    assert first["X-Api-Nonce"] != second["X-Api-Nonce"]
    assert first["X-Api-Timestamp"].endswith("Z")
