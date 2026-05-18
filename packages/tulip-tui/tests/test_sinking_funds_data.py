"""Unit tests for ``tulip_tui.data.sinking_funds``.

The adapter joins ``GET /v1/sinking-funds`` (every active fund visible
to the caller) with ``POST /v1/pools/balances`` (the batched balance
endpoint added in #137). Sinking funds that don't come back in the
balance response keep ``balance = None`` so the screen renders ``—``
instead of ``0.00`` (same convention as the envelopes / accounts
adapters).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient
from tulip_tui.data.sinking_funds import (
    SinkingFundsData,
    SinkingFundSummary,
    load_sinking_funds,
)

_CAR_REPAIR_ID = "11111111-1111-1111-1111-111111111111"
_VACATION_ID = "22222222-2222-2222-2222-222222222222"
_NEW_ID = "33333333-3333-3333-3333-333333333333"


def _sinking_funds_response() -> list[dict[str, object]]:
    return [
        {
            "id": _CAR_REPAIR_ID,
            "name": "Car repair",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "target_amount": "3000.00",
            "target_date": "2027-01-01",
            "contribution_strategy": "manual",
            "contribution_amount": None,
        },
        {
            "id": _VACATION_ID,
            "name": "Vacation",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "target_amount": "5000.00",
            "target_date": "2026-12-15",
            "contribution_strategy": "even_split",
            "contribution_amount": "250.00",
        },
        {
            "id": _NEW_ID,
            "name": "Brand new fund",
            "currency": "USD",
            "visibility": "shared",
            "is_active": True,
            "target_amount": "1000.00",
            "target_date": "2027-06-01",
            "contribution_strategy": "manual",
            "contribution_amount": None,
        },
    ]


def _balances_response() -> list[dict[str, object]]:
    return [
        {
            "pool_id": _CAR_REPAIR_ID,
            "name": "Car repair",
            "currency": "USD",
            "balance": "1200.00",
            "as_of": "2026-05-18",
        },
        {
            "pool_id": _VACATION_ID,
            "name": "Vacation",
            "currency": "USD",
            "balance": "650.00",
            "as_of": "2026-05-18",
        },
        # _NEW_ID intentionally absent — brand-new pool with no postings yet.
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


def test_load_sinking_funds_joins_balances() -> None:
    funds_payload = _sinking_funds_response()
    balances_payload = _balances_response()
    recorded: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/sinking-funds":
            recorded.append(("get", request.url.path))
            return httpx.Response(200, json=funds_payload)
        if request.method == "POST" and request.url.path == "/v1/pools/balances":
            import json as _json

            body = _json.loads(request.content.decode("utf-8"))
            recorded.append(("post", body))
            return httpx.Response(200, json=balances_payload)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_sinking_funds(client)

    assert isinstance(data, SinkingFundsData)
    assert len(data.sinking_funds) == 3
    by_id = {sf.id: sf for sf in data.sinking_funds}

    car = by_id[_CAR_REPAIR_ID]
    assert isinstance(car, SinkingFundSummary)
    assert car.name == "Car repair"
    assert car.currency == "USD"
    assert car.target_amount == "3000.00"
    assert car.target_date == "2027-01-01"
    assert car.contribution_strategy == "manual"
    assert car.contribution_amount is None
    assert car.balance == "1200.00"

    vacation = by_id[_VACATION_ID]
    assert vacation.contribution_strategy == "even_split"
    assert vacation.contribution_amount == "250.00"
    assert vacation.balance == "650.00"

    brand_new = by_id[_NEW_ID]
    # Missing from balances → None (screen renders "—" instead of 0.00).
    assert brand_new.balance is None

    posts = [body for kind, body in recorded if kind == "post"]
    assert posts == [{"pool_ids": [_CAR_REPAIR_ID, _VACATION_ID, _NEW_ID]}]


def test_load_sinking_funds_empty_short_circuits_balance_post() -> None:
    """No funds → no /v1/pools/balances POST at all."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/sinking-funds":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    with _build_client(httpx.MockTransport(handler)) as client:
        data = load_sinking_funds(client)

    assert data.sinking_funds == ()
    assert seen == ["GET /v1/sinking-funds"]


def test_load_sinking_funds_raises_cli_error_on_api_failure() -> None:
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
        load_sinking_funds(client)
