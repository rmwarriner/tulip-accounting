"""Unit tests for ``tulip_tui.data.transactions``.

Joins ``GET /v1/transactions`` (raw transaction rows + postings) with
``GET /v1/accounts`` (account UUID → human label lookup) so the screen
can render rows that read like ``Trader Joe's · Checking → Groceries``
without re-doing the join in the UI layer.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.transactions import (
    PostingSummary,
    TransactionsData,
    TransactionSummary,
    load_transactions,
)

_TX_1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TX_2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_CHECKING = "11111111-1111-1111-1111-111111111111"
_GROCERIES = "22222222-2222-2222-2222-222222222222"
_VISA = "33333333-3333-3333-3333-333333333333"


def _accounts_payload() -> list[dict[str, object]]:
    return [
        {
            "id": _CHECKING,
            "code": "assets:checking",
            "name": "Checking",
            "type": "asset",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        },
        {
            "id": _GROCERIES,
            "code": "expenses:groceries",
            "name": "Groceries",
            "type": "expense",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        },
        {
            "id": _VISA,
            "code": "liabilities:visa",
            "name": "Visa",
            "type": "liability",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        },
    ]


def _transactions_payload() -> list[dict[str, object]]:
    return [
        {
            "id": _TX_1,
            "date": "2026-05-14",
            "description": "Trader Joe's",
            "reference": None,
            "notes": None,
            "status": "posted",
            "postings": [
                {
                    "id": "p1",
                    "account_id": _CHECKING,
                    "amount": "-67.21",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
                {
                    "id": "p2",
                    "account_id": _GROCERIES,
                    "amount": "67.21",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
            ],
        },
        {
            "id": _TX_2,
            "date": "2026-05-12",
            "description": "Netflix",
            "reference": "INV-12345",
            "notes": "annual sub",
            "status": "pending",
            "postings": [
                {
                    "id": "p3",
                    "account_id": _VISA,
                    "amount": "-15.49",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
                {
                    "id": "p4",
                    "account_id": _GROCERIES,
                    "amount": "15.49",
                    "currency": "USD",
                    "memo": "subscriptions bucket",
                    "pool_id": None,
                },
            ],
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


def _handler_factory(
    *,
    tx_payload: list[dict[str, object]] | None = None,
    account_payload: list[dict[str, object]] | None = None,
    captured_params: dict[str, str] | None = None,
) -> httpx.MockTransport:
    """Build a mock transport that serves accounts + transactions."""

    accounts = account_payload if account_payload is not None else _accounts_payload()
    transactions = tx_payload if tx_payload is not None else _transactions_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts)
        if request.url.path == "/v1/transactions":
            if captured_params is not None:
                captured_params.update(dict(request.url.params))
            return httpx.Response(200, json=transactions)
        raise AssertionError(f"unexpected request: {request.url.path}")

    return httpx.MockTransport(handler)


def test_load_transactions_returns_summary_objects() -> None:
    """Each transaction becomes a ``TransactionSummary`` with typed postings."""
    with _build_client(_handler_factory()) as client:
        data = load_transactions(client)

    assert isinstance(data, TransactionsData)
    assert len(data.transactions) == 2
    first = data.transactions[0]
    assert isinstance(first, TransactionSummary)
    assert first.id == _TX_1
    assert first.date == "2026-05-14"
    assert first.description == "Trader Joe's"
    assert first.status == "posted"
    assert first.reference is None
    assert first.notes is None
    assert len(first.postings) == 2
    posting = first.postings[0]
    assert isinstance(posting, PostingSummary)
    assert posting.account_id == _CHECKING
    assert posting.account_label == "Checking"
    assert posting.amount == Decimal("-67.21")
    assert posting.currency == "USD"


def test_load_transactions_resolves_unknown_account_to_em_dash() -> None:
    """A posting against an unknown account renders ``—`` rather than the UUID."""
    tx_payload = [
        {
            "id": _TX_1,
            "date": "2026-05-14",
            "description": "Unknown",
            "reference": None,
            "notes": None,
            "status": "posted",
            "postings": [
                {
                    "id": "p1",
                    "account_id": "99999999-9999-9999-9999-999999999999",
                    "amount": "10.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
            ],
        },
    ]
    with _build_client(_handler_factory(tx_payload=tx_payload)) as client:
        data = load_transactions(client)

    assert data.transactions[0].postings[0].account_label == "—"


def test_load_transactions_passes_filter_params_through() -> None:
    """``account_id`` / ``status`` / ``from`` / ``to`` / ``limit`` reach the API."""
    captured: dict[str, str] = {}
    with _build_client(_handler_factory(captured_params=captured)) as client:
        load_transactions(
            client,
            account_id=_CHECKING,
            status="posted",
            date_from="2026-05-01",
            date_to="2026-05-31",
            limit=50,
        )

    assert captured == {
        "account_id": _CHECKING,
        "status": "posted",
        "from": "2026-05-01",
        "to": "2026-05-31",
        "limit": "50",
    }


def test_load_transactions_omits_filter_params_when_none() -> None:
    """Default call shape sends no query params."""
    captured: dict[str, str] = {}
    with _build_client(_handler_factory(captured_params=captured)) as client:
        load_transactions(client)

    assert captured == {}


def test_load_transactions_summary_carries_amount_text() -> None:
    """Each summary exposes a short, sign-bearing amount string for the table."""
    with _build_client(_handler_factory()) as client:
        data = load_transactions(client)

    # The "amount" the table renders is the sum of the positive postings
    # (i.e. the absolute movement) rendered with sign preserved on
    # spend-side transactions.
    first = data.transactions[0]
    assert first.amount_display == "-67.21 USD"

    second = data.transactions[1]
    assert second.amount_display == "-15.49 USD"


def test_load_transactions_handles_empty_result() -> None:
    with _build_client(_handler_factory(tx_payload=[])) as client:
        data = load_transactions(client)

    assert data.transactions == ()


def test_load_transactions_captures_tags() -> None:
    """Tags returned by the API land on the summary as a tuple of strings."""
    tx_payload = [
        {
            "id": _TX_1,
            "date": "2026-05-14",
            "description": "Trader Joe's",
            "reference": None,
            "notes": None,
            "status": "posted",
            "tags": ["food", "grocery"],
            "postings": [
                {
                    "id": "p1",
                    "account_id": _CHECKING,
                    "amount": "-20.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
                {
                    "id": "p2",
                    "account_id": _GROCERIES,
                    "amount": "20.00",
                    "currency": "USD",
                    "memo": None,
                    "pool_id": None,
                },
            ],
        }
    ]
    with _build_client(_handler_factory(tx_payload=tx_payload)) as client:
        data = load_transactions(client)

    assert data.transactions[0].tags == ("food", "grocery")


def test_load_transactions_empty_tags_when_absent() -> None:
    """When the API omits the tags field the summary has an empty tuple."""
    with _build_client(_handler_factory()) as client:
        data = load_transactions(client)

    for tx in data.transactions:
        assert tx.tags == ()


def test_load_transactions_raises_on_api_error() -> None:
    from tulip_cli.errors import CliError

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

    with _build_client(httpx.MockTransport(handler)) as client, pytest.raises(CliError):
        load_transactions(client)
