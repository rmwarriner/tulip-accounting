"""Unit tests for ``tulip_tui.data.pending``.

The adapter joins ``GET /v1/transactions?status=pending`` with
``GET /v1/accounts`` (UUID → name) and splits the result into two
groups at the stale-day boundary (14d default, per
`TUI_WIREFRAMES.md § Cross-cutting decisions § 3`). ``today`` is an
injected argument so tests don't depend on wall-clock.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient
from tulip_tui.data.pending import (
    PendingData,
    PendingTransaction,
    load_pending,
)

_CHECKING_ID = "11111111-1111-1111-1111-111111111111"
_VISA_ID = "22222222-2222-2222-2222-222222222222"
_GROCERIES_ID = "33333333-3333-3333-3333-333333333333"

_TODAY = date(2026, 5, 15)


def _accounts_response() -> list[dict[str, object]]:
    return [
        {
            "id": _CHECKING_ID,
            "code": "assets:checking",
            "name": "Checking",
            "type": "asset",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
        },
        {
            "id": _VISA_ID,
            "code": "liabilities:visa",
            "name": "Visa",
            "type": "liability",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
        },
        {
            "id": _GROCERIES_ID,
            "code": "expenses:groceries",
            "name": "Groceries",
            "type": "expense",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
        },
    ]


def _pending_response() -> list[dict[str, object]]:
    return [
        {
            # 23 days old → stale
            "id": "tx-old-check",
            "date": "2026-04-22",
            "description": "Check #1042 — Smith",
            "reference": "1042",
            "notes": None,
            "status": "pending",
            "postings": [
                {
                    "id": "p-1",
                    "account_id": _CHECKING_ID,
                    "amount": "-240.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
                {
                    "id": "p-2",
                    "account_id": _GROCERIES_ID,
                    "amount": "240.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
            ],
            "paired_shadow_tx_id": None,
            "voided_by_transaction_id": None,
            "voided_at": None,
            "tags": [],
        },
        {
            # 1 day old → recent
            "id": "tx-card-hold",
            "date": "2026-05-14",
            "description": "Card hold — Shell",
            "reference": None,
            "notes": None,
            "status": "pending",
            "postings": [
                {
                    "id": "p-3",
                    "account_id": _VISA_ID,
                    "amount": "-42.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
                {
                    "id": "p-4",
                    "account_id": _GROCERIES_ID,
                    "amount": "42.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
            ],
            "paired_shadow_tx_id": None,
            "voided_by_transaction_id": None,
            "voided_at": None,
            "tags": [],
        },
        {
            # exactly 14 days old → recent (boundary: stale is *strictly* >14d)
            "id": "tx-boundary",
            "date": "2026-05-01",
            "description": "ACH out — IRA",
            "reference": None,
            "notes": None,
            "status": "pending",
            "postings": [
                {
                    "id": "p-5",
                    "account_id": _CHECKING_ID,
                    "amount": "-500.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
                {
                    "id": "p-6",
                    "account_id": _GROCERIES_ID,
                    "amount": "500.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
            ],
            "paired_shadow_tx_id": None,
            "voided_by_transaction_id": None,
            "voided_at": None,
            "tags": [],
        },
    ]


class _FakeTokenStore:
    def load(self, _api_url: str) -> object:
        return SimpleNamespace(
            email="t@example.invalid",
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            access_expires_at=2**31 - 1,
        )

    def save(self, _api_url: str, _tokens: object) -> None: ...
    def clear(self, _api_url: str) -> None: ...


def _build_client(handler: httpx.MockTransport) -> TulipClient:
    return TulipClient(
        Config(api_url="https://example.invalid"),
        token_store=_FakeTokenStore(),  # type: ignore[arg-type]
        transport=handler,
    )


# ---- happy path -----------------------------------------------------


def test_load_pending_splits_at_stale_boundary() -> None:
    accounts_payload = _accounts_response()
    pending_payload = _pending_response()
    recorded: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append((request.method, str(request.url)))
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/transactions":
            assert request.url.params.get("status") == "pending"
            return httpx.Response(200, json=pending_payload)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_pending(client, today=_TODAY)

    assert isinstance(data, PendingData)
    assert len(data.stale) == 1
    assert len(data.recent) == 2

    stale = data.stale[0]
    assert isinstance(stale, PendingTransaction)
    assert stale.id == "tx-old-check"
    assert stale.age_days == 23
    assert stale.description == "Check #1042 — Smith"
    assert stale.reference == "1042"
    assert stale.account_label == "Checking"
    assert "-240.00" in stale.amount_display
    assert "USD" in stale.amount_display

    recent_ids = [t.id for t in data.recent]
    # Recent is sorted oldest-first within group (boundary 14d before card-hold 1d).
    assert recent_ids == ["tx-boundary", "tx-card-hold"]
    # exactly 14d → recent (stale is strict >14)
    assert data.recent[0].age_days == 14
    assert data.recent[1].age_days == 1


def test_load_pending_empty_returns_empty_groups() -> None:
    accounts_payload = _accounts_response()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/transactions":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_pending(client, today=_TODAY)

    assert data.stale == ()
    assert data.recent == ()


def test_load_pending_raises_cli_error_on_api_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "type": "/.well-known/errors/internal",
                "title": "Internal server error",
                "status": 500,
                "detail": "boom",
                "instance": "",
                "code": "internal",
            },
        )

    with (
        _build_client(httpx.MockTransport(handler)) as client,
        pytest.raises(CliError),
    ):
        load_pending(client, today=_TODAY)


def test_load_pending_unknown_account_renders_dash() -> None:
    """A posting referencing an account not in /v1/accounts → label '—'."""
    accounts_payload: list[dict[str, object]] = []  # empty lookup
    pending_payload = [
        {
            "id": "tx-orphan",
            "date": "2026-05-10",
            "description": "Mystery",
            "reference": None,
            "notes": None,
            "status": "pending",
            "postings": [
                {
                    "id": "p-x",
                    "account_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                    "amount": "-10.00",
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
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/transactions":
            return httpx.Response(200, json=pending_payload)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_pending(client, today=_TODAY)

    assert len(data.recent) == 1
    assert data.recent[0].account_label == "—"


def test_load_pending_custom_stale_days_threshold() -> None:
    """Caller can override the 14-day default for testing / alt config."""
    accounts_payload = _accounts_response()
    pending_payload = _pending_response()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/transactions":
            return httpx.Response(200, json=pending_payload)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        # threshold=2 → both boundary (14d) and old-check (23d) are stale.
        data = load_pending(client, today=_TODAY, stale_days=2)

    assert len(data.stale) == 2
    assert len(data.recent) == 1
    assert data.recent[0].id == "tx-card-hold"
