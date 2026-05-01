"""Tests for the unverified JWT-payload decoder used by ``tulip auth status``.

The helper does *not* validate the signature — it just base64-decodes
the middle segment of the token. The CLI doesn't have the API's signing
secret, so any local "validation" would be theatre. The next real call
will exercise the token; if the server rejects it, we drop it.
"""

from __future__ import annotations

import base64
import json

from tulip_cli.auth.jwt_decode import decode_jwt_payload


def _make_jwt(payload: dict[str, object]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"signature-bytes").rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def test_decode_round_trip() -> None:
    token = _make_jwt({"sub": "abc", "exp": 1_800_000_000, "role": "admin"})
    assert decode_jwt_payload(token) == {
        "sub": "abc",
        "exp": 1_800_000_000,
        "role": "admin",
    }


def test_decode_handles_payload_needing_padding() -> None:
    """JWT base64 strips ``=`` padding; the decoder restores it."""
    token = _make_jwt({"a": "b"})
    decoded = decode_jwt_payload(token)
    assert decoded == {"a": "b"}


def test_decode_rejects_malformed_token_returns_none() -> None:
    assert decode_jwt_payload("not.a.real.token.at.all") is None
    assert decode_jwt_payload("missing-segments") is None
    assert decode_jwt_payload("") is None


def test_decode_rejects_non_base64_payload() -> None:
    bad = "header.@@@-not-b64-@@@.signature"
    assert decode_jwt_payload(bad) is None


def test_decode_rejects_payload_that_is_not_a_json_object() -> None:
    payload = base64.urlsafe_b64encode(b'"just a string"').rstrip(b"=").decode()
    bad = f"hdr.{payload}.sig"
    assert decode_jwt_payload(bad) is None
