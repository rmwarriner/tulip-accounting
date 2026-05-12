"""Unit tests for ``_resolve_tx_id`` — the prefix-or-UUID resolver shared by
``transactions show / edit / void / delete``.

The integration coverage in ``test_p36_read_edit.py`` exercises the happy
path against a real API; here we use ``httpx.MockTransport`` so we can
drive the ambiguous-prefix and unknown-prefix branches deterministically
without needing to seed enough transactions to force a collision.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import typer

from tulip_cli.auth.tokens import TokenSet, TokenStore
from tulip_cli.commands.transactions import _resolve_tx_id
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient


def _config() -> Config:
    return Config(api_url="https://api.example.com")


def _client(tmp_path: Path, handler: httpx.MockTransport) -> TulipClient:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save(
        "https://api.example.com",
        TokenSet(
            email="alice@example.com",
            access_token="access",
            refresh_token="refresh",
            access_expires_at=9_999_999_999,
        ),
    )
    return TulipClient(_config(), token_store=store, transport=handler)


def test_resolve_tx_id_full_uuid_skips_api(tmp_path: Path) -> None:
    """A valid UUID is returned without any API round-trip."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(500, json={"unexpected": True})

    client = _client(tmp_path, httpx.MockTransport(handler))
    full = UUID("12345678-1234-1234-1234-123456789012")
    assert _resolve_tx_id(client, str(full), as_json=False) == full
    assert seen == []


def test_resolve_tx_id_unambiguous_prefix_resolves(tmp_path: Path) -> None:
    target = UUID("abcdef01-2345-6789-abcd-ef0123456789")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/transactions"
        assert request.url.params.get("id_prefix") == "abcdef01"
        return httpx.Response(
            200,
            json=[{"id": str(target), "description": "x"}],
        )

    client = _client(tmp_path, httpx.MockTransport(handler))
    assert _resolve_tx_id(client, "abcdef01", as_json=False) == target


def test_resolve_tx_id_ambiguous_prefix_raises(tmp_path: Path) -> None:
    matches = [
        {"id": "abcdef01-0000-0000-0000-000000000001"},
        {"id": "abcdef01-0000-0000-0000-000000000002"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=matches)

    client = _client(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(CliError) as excinfo:
        _resolve_tx_id(client, "abcdef01", as_json=False)
    assert excinfo.value.problem["code"] == "transaction.ambiguous_id_prefix"
    # Detail should include the prefix and a hint at the colliding ids.
    assert "abcdef01" in excinfo.value.problem["detail"]


def test_resolve_tx_id_no_match_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(CliError) as excinfo:
        _resolve_tx_id(client, "deadbeef", as_json=False)
    assert excinfo.value.problem["code"] == "transaction.not_found"


def test_resolve_tx_id_rejects_non_hex_input(tmp_path: Path) -> None:
    """Non-hex characters → typer.BadParameter before any API call."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(500)

    client = _client(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(typer.BadParameter):
        _resolve_tx_id(client, "not-a-uuid", as_json=False)
    assert seen == []


def test_resolve_tx_id_as_json_flag_propagates(tmp_path: Path) -> None:
    """``as_json=True`` makes the resulting CliError render JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client(tmp_path, httpx.MockTransport(handler))
    with pytest.raises(CliError) as excinfo:
        _resolve_tx_id(client, "deadbeef", as_json=True)
    assert excinfo.value.as_json is True
    # The body is still valid Problem Details.
    assert json.dumps(excinfo.value.problem)
