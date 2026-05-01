"""Tests for the transparent-refresh behavior in :class:`TulipClient`.

The strategy is **pre-emptive**: before each authenticated request the
client checks the access-token expiry locally; if it's within a small
leeway window of expiring, it calls ``POST /v1/auth/refresh`` first and
saves the new tokens. The original request then fires with the fresh
Bearer header.

Reactive (refresh on 401) was rejected because the API doesn't expose
a distinct ``auth.token_expired`` code — a reactive retry would also
kick in on legitimately bad tokens.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from tulip_cli.auth.tokens import TokenSet, TokenStore
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_AUTH, CliError
from tulip_cli.http import TulipClient


def _config() -> Config:
    return Config(api_url="https://api.example.com")


def _store(tmp_path: Path) -> TokenStore:
    return TokenStore(file_path=tmp_path / "tokens.json")


def _seed_tokens(store: TokenStore, *, expires_in: int) -> TokenSet:
    tokens = TokenSet(
        email="alice@example.com",
        access_token="initial.access.token",
        refresh_token="initial-refresh-token",
        access_expires_at=int(time.time()) + expires_in,
    )
    store.save("https://api.example.com", tokens)
    return tokens


def test_authenticated_request_uses_bearer_when_token_is_fresh(tmp_path: Path) -> None:
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True})

    store = _store(tmp_path)
    _seed_tokens(store, expires_in=900)

    client = TulipClient(
        _config(),
        token_store=store,
        transport=httpx.MockTransport(handler),
    )
    client.request("GET", "/v1/example", authenticated=True)
    assert seen_auth == ["Bearer initial.access.token"]


def test_expired_access_token_triggers_refresh_before_request(tmp_path: Path) -> None:
    requests_seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/v1/auth/refresh":
            body = json.loads(request.content)
            assert body == {"refresh_token": "initial-refresh-token"}
            return httpx.Response(
                200,
                json={
                    "access_token": "rotated.access.token",
                    "refresh_token": "rotated-refresh-token",
                    "token_type": "Bearer",
                    "expires_in": 900,
                },
            )
        return httpx.Response(200, json={"ok": True})

    store = _store(tmp_path)
    _seed_tokens(store, expires_in=5)  # within the leeway window

    client = TulipClient(
        _config(),
        token_store=store,
        transport=httpx.MockTransport(handler),
    )
    client.request("GET", "/v1/example", authenticated=True)

    assert requests_seen == [
        ("/v1/auth/refresh", None),
        ("/v1/example", "Bearer rotated.access.token"),
    ]
    persisted = store.load("https://api.example.com")
    assert persisted is not None
    assert persisted.access_token == "rotated.access.token"
    assert persisted.refresh_token == "rotated-refresh-token"


def test_authenticated_request_without_tokens_raises_not_logged_in(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request {request.url}")

    client = TulipClient(
        _config(),
        token_store=_store(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CliError) as exc_info:
        client.request("GET", "/v1/example", authenticated=True)
    assert exc_info.value.problem["code"] == "auth.not_logged_in"
    assert exc_info.value.exit_code == EXIT_AUTH


def test_refresh_failure_clears_tokens_and_raises_session_expired(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/refresh":
            return httpx.Response(
                401,
                content=json.dumps(
                    {
                        "type": "/.well-known/errors/auth.invalid_refresh_token",
                        "title": "Refresh token rejected",
                        "status": 401,
                        "detail": "...",
                        "instance": "/v1/auth/refresh",
                        "code": "auth.invalid_refresh_token",
                    }
                ).encode(),
                headers={"content-type": "application/problem+json"},
            )
        pytest.fail(f"unexpected request to {request.url}")

    store = _store(tmp_path)
    _seed_tokens(store, expires_in=5)

    client = TulipClient(
        _config(),
        token_store=store,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CliError) as exc_info:
        client.request("GET", "/v1/example", authenticated=True)
    assert exc_info.value.problem["code"] == "auth.session_expired"
    assert exc_info.value.exit_code == EXIT_AUTH
    assert store.load("https://api.example.com") is None


def test_unauthenticated_request_works_without_token_store(tmp_path: Path) -> None:
    """``register`` and other unauth'd commands shouldn't need a token store at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") is None
        return httpx.Response(201, json={"ok": True})

    client = TulipClient(_config(), transport=httpx.MockTransport(handler))
    response = client.post("/v1/auth/register", json={"x": 1})
    assert response.status_code == 201
