"""Pending transactions data adapter.

Joins ``GET /v1/transactions?status=pending`` with ``GET /v1/accounts``
(UUID → friendly name) and splits the result into two groups at the
stale-day boundary — 14 days by default, per
``TUI_WIREFRAMES.md § Cross-cutting decisions § 3``. ``today`` is an
injected argument so tests don't depend on wall-clock.

The split rule is strict: ``age > stale_days`` is stale; ``age <= stale_days``
is recent. Recent rows sort oldest-first to match the wireframe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from tulip_cli.http import TulipClient

STALE_DAYS_DEFAULT: int = 14


@dataclass(frozen=True, slots=True)
class PendingTransaction:
    """One row in the pending browser, with age + label pre-resolved."""

    id: str
    date: str
    age_days: int
    description: str
    reference: str | None
    account_label: str
    amount_display: str


@dataclass(frozen=True, slots=True)
class PendingData:
    """Pending transactions pre-split into Stale (>threshold) and Recent."""

    stale: tuple[PendingTransaction, ...]
    recent: tuple[PendingTransaction, ...]


def load_pending(
    client: TulipClient,
    *,
    today: date | None = None,
    stale_days: int = STALE_DAYS_DEFAULT,
) -> PendingData:
    """Fetch pending transactions, join account labels, group by staleness."""
    as_of = today if today is not None else datetime.now(UTC).date()

    accounts_raw = client.get("/v1/accounts", authenticated=True).json()
    label_by_id = {str(a["id"]): _account_label(a) for a in accounts_raw}

    tx_raw = client.get(
        "/v1/transactions",
        authenticated=True,
        params={"status": "pending"},
    ).json()

    summaries: list[PendingTransaction] = []
    for row in tx_raw:
        summaries.append(_to_summary(row, label_by_id, as_of))

    stale_list = [s for s in summaries if s.age_days > stale_days]
    recent_list = [s for s in summaries if s.age_days <= stale_days]

    # Stale: oldest first (largest age first). Recent: oldest first too —
    # matches the wireframe's "Stale (>14d)" then "Recent" ordering from
    # the most-actionable item downward.
    stale_list.sort(key=lambda s: s.age_days, reverse=True)
    recent_list.sort(key=lambda s: s.age_days, reverse=True)

    return PendingData(stale=tuple(stale_list), recent=tuple(recent_list))


def _account_label(account: dict[str, object]) -> str:
    """Use the friendly ``name`` (matches the wireframe); fall back to ``code``."""
    name = account.get("name")
    if isinstance(name, str) and name:
        return name
    code = account.get("code")
    return code if isinstance(code, str) and code else "—"


def _to_summary(
    row: dict[str, object],
    label_by_id: dict[str, str],
    as_of: date,
) -> PendingTransaction:
    raw_postings = cast("list[dict[str, object]]", row.get("postings") or [])

    # Pick the negative leg first (the account that paid) — same convention
    # as ``data/transactions.py``. Falls back to the first posting if there
    # are no negatives.
    negatives = [p for p in raw_postings if Decimal(str(p.get("amount", "0"))) < 0]
    chosen = negatives[0] if negatives else (raw_postings[0] if raw_postings else None)

    account_label = "—"
    amount_display = ""
    if chosen is not None:
        account_id = str(chosen.get("account_id", ""))
        account_label = label_by_id.get(account_id, "—")
        amount = Decimal(str(chosen.get("amount", "0"))).quantize(Decimal("0.01"))
        amount_display = f"{amount:,.2f} {chosen.get('currency', '')}"

    tx_date_raw = row.get("date", "")
    tx_date = (
        date.fromisoformat(tx_date_raw) if isinstance(tx_date_raw, str) and tx_date_raw else as_of
    )
    age_days = (as_of - tx_date).days

    return PendingTransaction(
        id=str(row.get("id", "")),
        date=str(tx_date_raw),
        age_days=age_days,
        description=str(row.get("description", "")),
        reference=_optional_str(row.get("reference")),
        account_label=account_label,
        amount_display=amount_display,
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__: list[str] = [
    "STALE_DAYS_DEFAULT",
    "PendingData",
    "PendingTransaction",
    "load_pending",
]
