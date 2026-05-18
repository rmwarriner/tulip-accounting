"""Sinking-funds-screen data adapter.

Composes two API reads into one immutable value object:

* ``GET /v1/sinking-funds`` — every active sinking fund visible to the
  caller.
* ``POST /v1/pools/balances`` — the batched balance endpoint added in
  #137. Same endpoint envelopes use; pools are pools.

The join is by sinking-fund id ↔ ``pool_id``. Funds with no balance
row keep ``balance = None`` so the screen renders ``—`` instead of a
misleading ``0.00`` (same convention as ``data/envelopes.py`` and
``data/accounts.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class SinkingFundSummary:
    """One row in the sinking-funds browser."""

    id: str
    name: str
    currency: str
    visibility: str
    is_active: bool
    target_amount: str
    target_date: str
    contribution_strategy: str
    contribution_amount: str | None
    balance: str | None


@dataclass(frozen=True, slots=True)
class SinkingFundsData:
    """The full payload the sinking-funds screen renders."""

    sinking_funds: tuple[SinkingFundSummary, ...]


def load_sinking_funds(client: TulipClient) -> SinkingFundsData:
    """Fetch + join ``/v1/sinking-funds`` and ``/v1/pools/balances``."""
    raw = client.get("/v1/sinking-funds", authenticated=True).json()
    if not raw:
        return SinkingFundsData(sinking_funds=())

    pool_ids = [str(row["id"]) for row in raw]
    balances_raw = client.post(
        "/v1/pools/balances",
        json={"pool_ids": pool_ids},
        authenticated=True,
    ).json()
    balances_by_id = {str(row["pool_id"]): str(row["balance"]) for row in balances_raw}

    summaries = tuple(_to_summary(row, balances_by_id) for row in raw)
    return SinkingFundsData(sinking_funds=summaries)


def _to_summary(row: dict[str, object], balances_by_id: dict[str, str]) -> SinkingFundSummary:
    fund_id = str(row.get("id", ""))
    return SinkingFundSummary(
        id=fund_id,
        name=str(row.get("name", "")),
        currency=str(row.get("currency", "")),
        visibility=str(row.get("visibility", "")),
        is_active=bool(row.get("is_active", False)),
        target_amount=str(row.get("target_amount", "")),
        target_date=str(row.get("target_date", "")),
        contribution_strategy=str(row.get("contribution_strategy", "")),
        contribution_amount=_optional_str(row.get("contribution_amount")),
        balance=balances_by_id.get(fund_id),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__: list[str] = [
    "SinkingFundSummary",
    "SinkingFundsData",
    "load_sinking_funds",
]
