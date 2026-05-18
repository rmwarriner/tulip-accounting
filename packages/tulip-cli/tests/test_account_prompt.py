"""Unit tests for ``tulip_cli._account_prompt`` (#196).

The helper intercepts ``account.not_found`` from ``_resolve_account``
during imports and offers to create the missing account inline, then
returns the created account dict so the caller can retry the import.

Non-TTY callers (scripts, CI, ``--json``) must get ``None`` immediately
so the caller can fall through to the legacy ``account.unknown`` error
path — no prompt, no hang.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest

from tulip_cli._account_prompt import prompt_create_missing_account
from tulip_cli.auth.tokens import TokenSet, TokenStore
from tulip_cli.config import Config
from tulip_cli.http import TulipClient

_API = "https://example.invalid"


def _seeded_store(tmp_path: Path) -> TokenStore:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save(
        _API,
        TokenSet(
            email="alice@example.com",
            access_token="access",
            refresh_token="refresh",
            access_expires_at=9_999_999_999,
        ),
    )
    return store


def _build_client(transport: httpx.MockTransport, tmp_path: Path) -> TulipClient:
    return TulipClient(
        Config(api_url=_API),
        token_store=_seeded_store(tmp_path),
        transport=transport,
    )


def _capture_handler(
    response_body: dict[str, Any] | None = None,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path == "/v1/accounts":
            return httpx.Response(
                201,
                json=response_body
                or {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "code": "assets:checking",
                    "name": "Checking",
                    "type": "asset",
                    "subtype": None,
                    "currency": "USD",
                    "visibility": "shared",
                    "is_active": True,
                    "parent_account_id": None,
                },
            )
        return httpx.Response(500, json={"unexpected": f"{request.method} {request.url.path}"})

    return httpx.MockTransport(_dispatch), seen


def test_returns_none_when_non_interactive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No TTY → no prompt, no POST, return ``None``."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = prompt_create_missing_account(client, "missing", as_json=False)
    assert result is None
    assert seen == []


def test_returns_none_in_json_mode_even_if_interactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--json`` callers never get a prompt regardless of TTY state."""
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = prompt_create_missing_account(client, "missing", as_json=True)
    assert result is None
    assert seen == []


def test_user_declines_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Answering ``n`` to the create-it prompt skips the POST and returns ``None``."""
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = prompt_create_missing_account(client, "missing", as_json=False)
    assert result is None
    assert seen == []


def test_creates_account_and_returns_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Answering ``y`` walks through the fields and POSTs to /v1/accounts."""
    # Sequence: confirm-create, name, type, currency, code (blank → no code).
    monkeypatch.setattr("sys.stdin", io.StringIO("y\nChecking\nasset\nUSD\n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _capture_handler(
        response_body={
            "id": "22222222-2222-2222-2222-222222222222",
            "code": None,
            "name": "Checking",
            "type": "asset",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        }
    )
    with _build_client(transport, tmp_path) as client:
        result = prompt_create_missing_account(client, "Checking", as_json=False)

    assert result is not None
    assert result["id"] == "22222222-2222-2222-2222-222222222222"
    assert len(seen) == 1
    posted_request = seen[0]
    assert posted_request.method == "POST"
    assert posted_request.url.path == "/v1/accounts"
    import json as _json

    body = _json.loads(posted_request.content)
    assert body["name"] == "Checking"
    assert body["type"] == "asset"
    assert body["currency"] == "USD"
    assert "code" not in body  # blank code → field omitted


def test_smart_default_code_from_numeric_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A purely numeric identifier prefills ``--code`` automatically."""
    # confirm-create, name, type, currency, code (accept default by hitting enter).
    monkeypatch.setattr("sys.stdin", io.StringIO("y\nChecking\nasset\nUSD\n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        prompt_create_missing_account(client, "1010", as_json=False)

    import json as _json

    body = _json.loads(seen[0].content)
    # The numeric identifier flowed into ``code`` as the default; the
    # user accepted by leaving the prompt blank.
    assert body["code"] == "1010"


def test_smart_default_name_from_hierarchical_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ``assets:checking`` identifier prefills both ``code`` and ``name``."""
    # confirm, name (accept default), type, currency, code (accept default).
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n\nasset\nUSD\n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        prompt_create_missing_account(client, "assets:checking", as_json=False)

    import json as _json

    body = _json.loads(seen[0].content)
    assert body["name"] == "checking"  # leaf segment, case-preserving from input
    assert body["code"] == "assets:checking"


def test_rejects_invalid_account_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An invalid type re-prompts; second valid answer wins."""
    # confirm, name, type=bogus (rejected), type=asset (accepted), currency, code (blank).
    monkeypatch.setattr("sys.stdin", io.StringIO("y\nChecking\nbogus\nasset\nUSD\n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = prompt_create_missing_account(client, "Checking", as_json=False)

    assert result is not None
    import json as _json

    body = _json.loads(seen[0].content)
    assert body["type"] == "asset"


def test_eof_during_prompt_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ctrl-D / EOF during the prompt cancels cleanly without raising."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # immediate EOF
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = prompt_create_missing_account(client, "Checking", as_json=False)

    assert result is None
    assert seen == []


# -- _resolve_or_offer_create wiring (imports.py) ------------------------


def test_resolve_or_offer_create_passes_through_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: existing account resolves normally; no prompt fires."""
    from tulip_cli.commands import imports as imports_mod

    expected = {"id": "abc", "code": "assets:checking", "name": "Checking"}
    monkeypatch.setattr(imports_mod, "_resolve_account", lambda _c, _i: expected)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # would EOF if prompted
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, _ = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = imports_mod._resolve_or_offer_create(client, "assets:checking", as_json=False)
    assert result == expected


def test_resolve_or_offer_create_reraises_non_not_found_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A different error code (e.g. ambiguous_code) bubbles out unchanged."""
    from tulip_cli.commands import imports as imports_mod
    from tulip_cli.errors import CliError

    def _raise(_c: object, _i: str) -> dict[str, Any]:
        raise CliError(
            problem={"code": "account.ambiguous_code", "title": "ambig", "status": 0},
            as_json=False,
        )

    monkeypatch.setattr(imports_mod, "_resolve_account", _raise)

    transport, _ = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        with pytest.raises(CliError) as excinfo:
            imports_mod._resolve_or_offer_create(client, "checking", as_json=False)
    assert excinfo.value.problem["code"] == "account.ambiguous_code"


def test_resolve_or_offer_create_reraises_when_user_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User declines the create-it prompt → original ``account.not_found`` re-raises."""
    from tulip_cli.commands import imports as imports_mod
    from tulip_cli.errors import CliError

    def _raise(_c: object, _i: str) -> dict[str, Any]:
        raise CliError(
            problem={"code": "account.not_found", "title": "missing", "status": 0},
            as_json=False,
        )

    monkeypatch.setattr(imports_mod, "_resolve_account", _raise)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, _ = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        with pytest.raises(CliError) as excinfo:
            imports_mod._resolve_or_offer_create(client, "checking", as_json=False)
    assert excinfo.value.problem["code"] == "account.not_found"


def test_resolve_or_offer_create_returns_created_account_on_accept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accepting the prompt returns the newly-created account so the caller can retry."""
    from tulip_cli.commands import imports as imports_mod
    from tulip_cli.errors import CliError

    def _raise(_c: object, _i: str) -> dict[str, Any]:
        raise CliError(
            problem={"code": "account.not_found", "title": "missing", "status": 0},
            as_json=False,
        )

    monkeypatch.setattr(imports_mod, "_resolve_account", _raise)
    monkeypatch.setattr("sys.stdin", io.StringIO("y\nChecking\nasset\nUSD\n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    transport, seen = _capture_handler()
    with _build_client(transport, tmp_path) as client:
        result = imports_mod._resolve_or_offer_create(client, "checking", as_json=False)
    assert result is not None
    assert result["id"] == "11111111-1111-1111-1111-111111111111"
    # One POST /v1/accounts from the inline create.
    assert [r.method + " " + r.url.path for r in seen] == ["POST /v1/accounts"]
