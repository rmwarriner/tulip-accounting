"""Unit tests for ``tulip_tui.data.envelopes``.

The adapter joins ``GET /v1/envelopes`` (every active envelope visible
to the caller) with ``POST /v1/pools/balances`` (the batched balance
endpoint added in #137). Envelopes that don't come back in the
balance response keep ``balance = None`` so the screen can render
``—`` instead of a misleading ``0.00`` (same convention as the
accounts adapter).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient
from tulip_tui.data.envelopes import (
    EnvelopesData,
    EnvelopeSummary,
    load_envelopes,
    summarize_refill_rule,
)

_GROCERIES_ID = "11111111-1111-1111-1111-111111111111"
_GAS_ID = "22222222-2222-2222-2222-222222222222"
_DINING_ID = "33333333-3333-3333-3333-333333333333"
_NEW_ID = "44444444-4444-4444-4444-444444444444"


def _envelopes_response() -> list[dict[str, object]]:
    return [
        {
            "id": _GROCERIES_ID,
            "name": "Groceries",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "budget_period": "monthly",
            "rollover_policy": "reset",
            "budget_amount": "600.00",
            "refill_rule": {
                "strategy": "fixed_amount",
                "amount": "600.00",
                "currency": "USD",
            },
        },
        {
            "id": _GAS_ID,
            "name": "Gas",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "budget_period": "monthly",
            "rollover_policy": "accumulate",
            "budget_amount": "200.00",
            "refill_rule": {
                "strategy": "fill_to_amount",
                "amount": "200.00",
                "currency": "USD",
            },
        },
        {
            "id": _DINING_ID,
            "name": "Dining out",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "budget_period": "monthly",
            "rollover_policy": "cap_at_budget",
            "budget_amount": None,
            "refill_rule": {
                "strategy": "percentage_of_income",
                "percentage": "0.05",
            },
        },
        {
            "id": _NEW_ID,
            "name": "Brand new",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "budget_period": "monthly",
            "rollover_policy": "reset",
            "budget_amount": "100.00",
            "refill_rule": None,
        },
    ]


def _balances_response() -> list[dict[str, object]]:
    return [
        {
            "pool_id": _GROCERIES_ID,
            "name": "Groceries",
            "currency": "USD",
            "balance": "187.45",
            "as_of": "2026-05-17",
        },
        {
            "pool_id": _GAS_ID,
            "name": "Gas",
            "currency": "USD",
            "balance": "111.60",
            "as_of": "2026-05-17",
        },
        {
            "pool_id": _DINING_ID,
            "name": "Dining out",
            "currency": "USD",
            "balance": "34.90",
            "as_of": "2026-05-17",
        },
        # _NEW_ID intentionally absent — brand-new pool, no postings yet.
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


def test_load_envelopes_joins_balances_and_refill_summaries() -> None:
    envelopes_payload = _envelopes_response()
    balances_payload = _balances_response()
    recorded: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/envelopes":
            recorded.append(("get", request.url.path))
            return httpx.Response(200, json=envelopes_payload)
        if request.method == "POST" and request.url.path == "/v1/pools/balances":
            import json as _json

            body = _json.loads(request.content.decode("utf-8"))
            recorded.append(("post", body))
            return httpx.Response(200, json=balances_payload)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_envelopes(client)

    assert isinstance(data, EnvelopesData)
    assert len(data.envelopes) == 4
    by_id = {e.id: e for e in data.envelopes}

    groceries = by_id[_GROCERIES_ID]
    assert isinstance(groceries, EnvelopeSummary)
    assert groceries.name == "Groceries"
    assert groceries.currency == "USD"
    assert groceries.budget_period == "monthly"
    assert groceries.rollover_policy == "reset"
    assert groceries.budget_amount == "600.00"
    assert groceries.balance == "187.45"
    assert "fixed" in groceries.refill_summary

    gas = by_id[_GAS_ID]
    assert gas.balance == "111.60"
    assert "target" in gas.refill_summary

    dining = by_id[_DINING_ID]
    assert dining.budget_amount is None
    assert dining.balance == "34.90"
    assert "pct-inflow" in dining.refill_summary
    assert "5%" in dining.refill_summary

    brand_new = by_id[_NEW_ID]
    # Missing from balances → None (so screen renders "—" instead of 0).
    assert brand_new.balance is None
    assert brand_new.refill_summary == "—"

    # The POST body carried exactly the pool ids the GET returned, in order.
    posts = [body for kind, body in recorded if kind == "post"]
    assert posts == [{"pool_ids": [_GROCERIES_ID, _GAS_ID, _DINING_ID, _NEW_ID]}]


def test_load_envelopes_empty_short_circuits_balance_post() -> None:
    """No envelopes → no /v1/pools/balances POST at all."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/envelopes":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_envelopes(client)

    assert data.envelopes == ()
    assert seen == ["GET /v1/envelopes"]


def test_load_envelopes_raises_cli_error_on_api_failure() -> None:
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
        load_envelopes(client)


# ---- refill-summary helper -----------------------------------------


@pytest.mark.parametrize(
    ("rule", "expected_substring"),
    [
        ({"strategy": "fixed_amount", "amount": "100.00", "currency": "USD"}, "fixed: 100.00"),
        ({"strategy": "fill_to_amount", "amount": "500.00", "currency": "USD"}, "target: 500.00"),
        ({"strategy": "percentage_of_income", "percentage": "0.05"}, "5%"),
        ({"strategy": "percentage_of_income", "percentage": "0.125"}, "12.5%"),
        ({"strategy": "percentage_of_income"}, "pct-inflow"),
        ({"strategy": "something_new"}, "something_new"),
    ],
)
def test_summarize_refill_rule_strategy_branches(
    rule: dict[str, object], expected_substring: str
) -> None:
    summary = summarize_refill_rule(rule)
    assert expected_substring in summary


def test_summarize_refill_rule_none_returns_dash() -> None:
    assert summarize_refill_rule(None) == "—"
