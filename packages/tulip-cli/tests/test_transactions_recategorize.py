"""Unit tests for ``tulip transactions recategorize``.

Uses ``httpx.MockTransport`` to drive the request dispatch without a
live API: verifies PATCH for PENDING vs POST /replace for POSTED, the
description-contains filter, and dry-run behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import typer
from typer.testing import CliRunner

from tulip_cli.auth.tokens import TokenSet, TokenStore
from tulip_cli.main import app

_API = "https://api.example.com"
_FROM = UUID("11111111-1111-1111-1111-111111111111")
_TO = UUID("22222222-2222-2222-2222-222222222222")
_BANK = UUID("33333333-3333-3333-3333-333333333333")
_PENDING_TX = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_POSTED_TX = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _seed_token_store(tmp_path: Path) -> TokenStore:
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


def _account_row(account_id: UUID, name: str, code: str | None = None) -> dict:
    return {
        "id": str(account_id),
        "name": name,
        "code": code,
        "type": "expense",
        "subtype": None,
        "currency": "USD",
        "visibility": "shared",
        "is_active": True,
        "is_placeholder": False,
        "parent_account_id": None,
        "tags": [],
        "notes": None,
    }


def _tx_row(
    *,
    tx_id: UUID,
    status: str,
    description: str,
    from_account: UUID = _FROM,
) -> dict:
    return {
        "id": str(tx_id),
        "date": "2026-05-01",
        "description": description,
        "reference": None,
        "notes": None,
        "status": status,
        "postings": [
            {
                "id": "11000000-0000-0000-0000-000000000001",
                "account_id": str(_BANK),
                "amount": "-12.50",
                "currency": "USD",
                "memo": None,
                "pool_id": None,
            },
            {
                "id": "11000000-0000-0000-0000-000000000002",
                "account_id": str(from_account),
                "amount": "12.50",
                "currency": "USD",
                "memo": None,
                "pool_id": None,
            },
        ],
        "paired_shadow_tx_id": None,
        "voided_by_transaction_id": None,
        "voided_at": None,
        "tags": [],
    }


def _build_handler(captured: list[httpx.Request]) -> httpx.MockTransport:
    """Return a transport that handles the recategorize flow."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    _account_row(_FROM, "Imbalance: Unknown", code=None),
                    _account_row(_TO, "Groceries", code="51310"),
                    _account_row(_BANK, "Checking", code="1110"),
                ],
            )
        if path == f"/v1/accounts/{_FROM}":
            return httpx.Response(200, json=_account_row(_FROM, "Imbalance: Unknown"))
        if path == f"/v1/accounts/{_TO}":
            return httpx.Response(200, json=_account_row(_TO, "Groceries", code="51310"))
        if path == f"/v1/accounts/{_BANK}":
            return httpx.Response(200, json=_account_row(_BANK, "Checking", code="1110"))
        if path == "/v1/transactions" and request.method == "GET":
            status = request.url.params.get("status")
            account_id_param = request.url.params.get("account_id")
            assert account_id_param == str(_FROM)
            if status == "pending":
                return httpx.Response(
                    200,
                    json=[
                        _tx_row(tx_id=_PENDING_TX, status="pending", description="Walmart"),
                        _tx_row(
                            tx_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"),
                            status="pending",
                            description="Target",
                        ),
                    ],
                )
            if status == "posted":
                return httpx.Response(
                    200,
                    json=[_tx_row(tx_id=_POSTED_TX, status="posted", description="Walmart")],
                )
            return httpx.Response(200, json=[])
        if path == f"/v1/transactions/{_PENDING_TX}" and request.method == "PATCH":
            body = json.loads(request.content.decode())
            assert any(p["account_id"] == str(_TO) for p in body["postings"])
            return httpx.Response(
                200,
                json=_tx_row(tx_id=_PENDING_TX, status="pending", description="Walmart"),
            )
        if path == f"/v1/transactions/{_POSTED_TX}/replace" and request.method == "POST":
            body = json.loads(request.content.decode())
            assert any(p["account_id"] == str(_TO) for p in body["postings"])
            assert body.get("reason")
            return httpx.Response(
                201,
                json={
                    "source_id": str(_POSTED_TX),
                    "reversal_id": "44444444-4444-4444-4444-444444444444",
                    "replacement_id": "55555555-5555-5555-5555-555555555555",
                    "voided_at": "2026-05-21T00:00:00+00:00",
                    "paired_shadow_tx_id_voided": None,
                    "paired_shadow_tx_id_for_replacement": None,
                },
            )
        return httpx.Response(404, json={"path": path})

    return httpx.MockTransport(handler)


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """CliRunner wired so the CLI uses our mock transport + seeded tokens."""
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    _seed_token_store(tmp_path)
    return CliRunner()


def _invoke(
    runner: CliRunner,
    captured: list[httpx.Request],
    *args: str,
    input_: str | None = None,
) -> typer.testing.Result:
    import tulip_cli.http as http_mod

    real_init = http_mod.TulipClient.__init__

    def patched_init(self, config, *, token_store=None, as_json=False, transport=None):
        if transport is None:
            transport = _build_handler(captured)
        real_init(
            self,
            config,
            token_store=token_store,
            as_json=as_json,
            transport=transport,
        )

    http_mod.TulipClient.__init__ = patched_init
    try:
        return runner.invoke(
            app,
            ["--api-url", _API, *args],
            input=input_,
        )
    finally:
        http_mod.TulipClient.__init__ = real_init


def test_recategorize_dry_run_lists_planned_changes(runner: CliRunner) -> None:
    """--dry-run prints planned re-targets without issuing PATCH/POST."""
    captured: list[httpx.Request] = []
    result = _invoke(
        runner,
        captured,
        "transactions",
        "recategorize",
        "--from",
        str(_FROM),
        "--to",
        str(_TO),
        "--description-contains",
        "Walmart",
        "--dry-run",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    assert "would re-target" in result.output
    assert "Would update 1 of 1" in result.output
    # No write requests issued during dry-run.
    write_requests = [
        r
        for r in captured
        if r.method in ("PATCH", "POST")
        and "/v1/transactions/" in r.url.path
        and r.url.path.endswith("/replace") is not None
    ]
    # Only the /v1/transactions list calls are GETs; verify zero writes.
    assert all(r.method == "GET" for r in captured if "/v1/transactions" in r.url.path)
    del write_requests  # unused; kept for the comment above


def test_recategorize_pending_uses_patch(runner: CliRunner) -> None:
    """PENDING transactions are dispatched via PATCH /v1/transactions/{id}."""
    captured: list[httpx.Request] = []
    result = _invoke(
        runner,
        captured,
        "transactions",
        "recategorize",
        "--from",
        str(_FROM),
        "--to",
        str(_TO),
        "--description-contains",
        "Walmart",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    patch_calls = [r for r in captured if r.method == "PATCH"]
    assert len(patch_calls) == 1
    assert str(_PENDING_TX) in patch_calls[0].url.path
    assert "Updated 1 of 1" in result.output


def test_recategorize_posted_with_include_flag_uses_replace(
    runner: CliRunner,
) -> None:
    """POSTED transactions are dispatched via POST /v1/transactions/{id}/replace
    only when ``--include-posted`` is set."""
    captured: list[httpx.Request] = []
    result = _invoke(
        runner,
        captured,
        "transactions",
        "recategorize",
        "--from",
        str(_FROM),
        "--to",
        str(_TO),
        "--description-contains",
        "Walmart",
        "--include-posted",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    replace_calls = [r for r in captured if r.method == "POST" and r.url.path.endswith("/replace")]
    assert len(replace_calls) == 1
    assert str(_POSTED_TX) in replace_calls[0].url.path
    patch_calls = [r for r in captured if r.method == "PATCH"]
    # 1 PATCH (pending) + 1 replace (posted)
    assert len(patch_calls) == 1
    assert "via void+replace" in result.output


def test_recategorize_same_account_rejects(runner: CliRunner) -> None:
    """--from and --to resolving to the same id is rejected."""
    captured: list[httpx.Request] = []
    result = _invoke(
        runner,
        captured,
        "transactions",
        "recategorize",
        "--from",
        str(_FROM),
        "--to",
        str(_FROM),
        "--yes",
    )
    assert result.exit_code != 0
    assert "same account" in result.output.lower()
