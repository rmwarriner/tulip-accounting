"""Unit tests for ``tulip_tui.data.accounts``.

The data layer composes ``GET /v1/accounts`` (every active account in
the household, including those with no postings) with
``GET /v1/reports/trial-balance`` (balances per account in its currency)
into a single value object that screens can render without touching
JSON or httpx. Accounts with no postings get a ``None`` balance so the
screen can distinguish "zero" from "never posted".
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.http import TulipClient
from tulip_tui.data.accounts import (
    AccountsData,
    AccountSummary,
    CurrencyTotal,
    load_accounts,
)

_ACCOUNT_CHECKING_ID = "11111111-1111-1111-1111-111111111111"
_ACCOUNT_SAVINGS_ID = "22222222-2222-2222-2222-222222222222"
_ACCOUNT_VISA_ID = "33333333-3333-3333-3333-333333333333"
_ACCOUNT_EXPENSE_ID = "44444444-4444-4444-4444-444444444444"
_ACCOUNT_NO_POSTING_ID = "55555555-5555-5555-5555-555555555555"


def _accounts_response() -> list[dict[str, object]]:
    return [
        {
            "id": _ACCOUNT_CHECKING_ID,
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
            "id": _ACCOUNT_SAVINGS_ID,
            "code": "assets:savings",
            "name": "Savings",
            "type": "asset",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        },
        {
            "id": _ACCOUNT_VISA_ID,
            "code": "liabilities:visa",
            "name": "Visa",
            "type": "liability",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        },
        {
            "id": _ACCOUNT_EXPENSE_ID,
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
            "id": _ACCOUNT_NO_POSTING_ID,
            "code": "assets:vacation-fund",
            "name": "Vacation Fund",
            "type": "asset",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
        },
    ]


def _trial_balance_response() -> dict[str, object]:
    return {
        "as_of": "2026-05-17",
        "rows": [
            {
                "account_id": _ACCOUNT_CHECKING_ID,
                "code": "assets:checking",
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "balance": "3241.18",
                "has_pending": False,
            },
            {
                "account_id": _ACCOUNT_SAVINGS_ID,
                "code": "assets:savings",
                "name": "Savings",
                "type": "asset",
                "currency": "USD",
                "balance": "12500.00",
                "has_pending": False,
            },
            {
                "account_id": _ACCOUNT_VISA_ID,
                "code": "liabilities:visa",
                "name": "Visa",
                "type": "liability",
                "currency": "USD",
                "balance": "-842.55",
                "has_pending": False,
            },
            {
                "account_id": _ACCOUNT_EXPENSE_ID,
                "code": "expenses:groceries",
                "name": "Groceries",
                "type": "expense",
                "currency": "USD",
                "balance": "256.40",
                "has_pending": False,
            },
        ],
        "totals_by_currency": [
            {"currency": "USD", "debits": "15997.58", "credits": "-842.55"},
        ],
        "pending_included": False,
        "pending_count": 0,
    }


class _FakeTokenStore:
    """Stand-in for ``tulip_cli.auth.tokens.TokenStore``.

    Returns a token whose ``access_expires_at`` is far enough in the
    future that ``TulipClient`` never tries the refresh path during a
    test. ``save`` / ``clear`` are no-ops because the mock transport
    never returns a 4xx that would trigger them.
    """

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


def test_load_accounts_joins_accounts_and_trial_balance() -> None:
    """``load_accounts`` joins by ``id`` / ``account_id`` and fills balances."""
    accounts_payload = _accounts_response()
    trial_payload = _trial_balance_response()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/reports/trial-balance":
            return httpx.Response(200, json=trial_payload)
        raise AssertionError(f"unexpected request: {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_accounts(client)

    assert isinstance(data, AccountsData)
    assert data.as_of == "2026-05-17"
    by_id = {a.id: a for a in data.accounts}
    assert by_id[_ACCOUNT_CHECKING_ID].balance == Decimal("3241.18")
    assert by_id[_ACCOUNT_SAVINGS_ID].balance == Decimal("12500.00")
    assert by_id[_ACCOUNT_VISA_ID].balance == Decimal("-842.55")
    assert by_id[_ACCOUNT_EXPENSE_ID].balance == Decimal("256.40")
    # Vacation Fund has no trial-balance row → balance is None, not zero.
    assert by_id[_ACCOUNT_NO_POSTING_ID].balance is None


def test_load_accounts_surfaces_account_metadata() -> None:
    """Each summary carries the metadata the screen needs to render rows."""
    accounts_payload = _accounts_response()
    trial_payload = _trial_balance_response()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/reports/trial-balance":
            return httpx.Response(200, json=trial_payload)
        raise AssertionError(f"unexpected request: {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_accounts(client)

    checking = next(a for a in data.accounts if a.id == _ACCOUNT_CHECKING_ID)
    assert isinstance(checking, AccountSummary)
    assert checking.code == "assets:checking"
    assert checking.name == "Checking"
    assert checking.type == "asset"
    assert checking.currency == "USD"


def test_load_accounts_groups_by_type_with_subtotals_per_currency() -> None:
    """Per-group subtotals roll up the balances in each currency."""
    accounts_payload = _accounts_response()
    trial_payload = _trial_balance_response()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/reports/trial-balance":
            return httpx.Response(200, json=trial_payload)
        raise AssertionError(f"unexpected request: {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_accounts(client)

    # Five accounts → three type groups: asset, liability, expense. Each
    # group has one ``CurrencyTotal`` per distinct currency in the group;
    # the Vacation Fund (no postings) does not contribute to the subtotal.
    by_type = {g.type: g for g in data.groups}
    assert set(by_type) == {"asset", "liability", "expense"}
    assert by_type["asset"].totals == (CurrencyTotal(currency="USD", amount=Decimal("15741.18")),)
    assert by_type["liability"].totals == (
        CurrencyTotal(currency="USD", amount=Decimal("-842.55")),
    )
    assert by_type["expense"].totals == (CurrencyTotal(currency="USD", amount=Decimal("256.40")),)


def test_load_accounts_handles_empty_household() -> None:
    """No accounts → empty ``AccountsData`` with today's ``as_of`` echoed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=[])
        if request.url.path == "/v1/reports/trial-balance":
            return httpx.Response(
                200,
                json={
                    "as_of": "2026-05-17",
                    "rows": [],
                    "totals_by_currency": [],
                    "pending_included": False,
                    "pending_count": 0,
                },
            )
        raise AssertionError(f"unexpected request: {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_accounts(client)

    assert data.accounts == ()
    assert data.groups == ()
    assert data.as_of == "2026-05-17"


def test_load_accounts_captures_tags() -> None:
    """Tags returned by the API land on the AccountSummary as a tuple of strings."""
    accounts_payload = [
        {
            "id": _ACCOUNT_CHECKING_ID,
            "code": "assets:checking",
            "name": "Checking",
            "type": "asset",
            "subtype": None,
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "parent_account_id": None,
            "tags": ["liquid", "primary"],
        },
    ]
    trial_payload = {
        "as_of": "2026-05-17",
        "rows": [],
        "totals_by_currency": [],
        "pending_included": False,
        "pending_count": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/reports/trial-balance":
            return httpx.Response(200, json=trial_payload)
        raise AssertionError(f"unexpected: {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_accounts(client)

    assert data.accounts[0].tags == ("liquid", "primary")


def test_load_accounts_empty_tags_when_absent() -> None:
    """Accounts without a ``tags`` key get an empty tuple, not an error."""
    accounts_payload = _accounts_response()
    trial_payload = _trial_balance_response()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json=accounts_payload)
        if request.url.path == "/v1/reports/trial-balance":
            return httpx.Response(200, json=trial_payload)
        raise AssertionError(f"unexpected: {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_accounts(client)

    for acct in data.accounts:
        assert acct.tags == ()


def test_load_accounts_raises_when_api_returns_error() -> None:
    """API errors bubble out as ``CliError`` for the screen to surface."""
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
        load_accounts(client)
