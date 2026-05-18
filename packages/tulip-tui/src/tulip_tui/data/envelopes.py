"""Envelopes-screen data adapter.

Composes two API reads into one immutable value object:

* ``GET /v1/envelopes`` — every active envelope visible to the caller.
* ``POST /v1/pools/balances`` — the batched balance endpoint added in
  #137. One round-trip across every envelope id; the CLI ``envelopes
  list`` command uses the same pattern.

The join is by envelope id ↔ ``pool_id``. Envelopes with no balance
row keep ``balance = None`` so the screen renders ``—`` instead of a
misleading ``0.00`` (same convention as the accounts adapter).

The refill-rule summariser is duplicated from
``tulip_cli.commands._pools._summarize_refill_rule`` rather than
reaching into a ``_``-prefixed CLI internal across the package
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class EnvelopeSummary:
    """One row in the envelope browser."""

    id: str
    name: str
    currency: str
    visibility: str
    is_active: bool
    budget_period: str
    rollover_policy: str
    budget_amount: str | None
    balance: str | None
    refill_summary: str


@dataclass(frozen=True, slots=True)
class EnvelopesData:
    """The full payload the envelopes screen renders."""

    envelopes: tuple[EnvelopeSummary, ...]


def load_envelopes(client: TulipClient) -> EnvelopesData:
    """Fetch + join ``/v1/envelopes`` and ``/v1/pools/balances``."""
    raw = client.get("/v1/envelopes", authenticated=True).json()
    if not raw:
        return EnvelopesData(envelopes=())

    pool_ids = [str(row["id"]) for row in raw]
    balances_raw = client.post(
        "/v1/pools/balances",
        json={"pool_ids": pool_ids},
        authenticated=True,
    ).json()
    balances_by_id = {str(row["pool_id"]): str(row["balance"]) for row in balances_raw}

    summaries = tuple(_to_summary(row, balances_by_id) for row in raw)
    return EnvelopesData(envelopes=summaries)


def _to_summary(row: dict[str, object], balances_by_id: dict[str, str]) -> EnvelopeSummary:
    envelope_id = str(row.get("id", ""))
    return EnvelopeSummary(
        id=envelope_id,
        name=str(row.get("name", "")),
        currency=str(row.get("currency", "")),
        visibility=str(row.get("visibility", "")),
        is_active=bool(row.get("is_active", False)),
        budget_period=str(row.get("budget_period", "")),
        rollover_policy=str(row.get("rollover_policy", "")),
        budget_amount=_optional_str(row.get("budget_amount")),
        balance=balances_by_id.get(envelope_id),
        refill_summary=summarize_refill_rule(_optional_dict(row.get("refill_rule"))),
    )


def summarize_refill_rule(rule: dict[str, object] | None) -> str:
    """One-line description of an envelope's ``refill_rule``.

    Mirrors ``tulip_cli.commands._pools._summarize_refill_rule`` so the
    TUI doesn't import a CLI private. Each strategy gets a compact
    form keyed by what a user actually scans for.
    """
    if not rule:
        return "—"
    strategy = rule.get("strategy")
    if strategy == "fixed_amount":
        amount = rule.get("amount", "?")
        currency = rule.get("currency", "")
        return f"fixed: {amount} {currency}".rstrip()
    if strategy == "fill_to_amount":
        amount = rule.get("amount", "?")
        currency = rule.get("currency", "")
        return f"target: {amount} {currency}".rstrip()
    if strategy == "percentage_of_income":
        pct = rule.get("percentage")
        if pct is None:
            return "pct-inflow"
        try:
            display = f"{float(str(pct)) * 100:g}%"
        except (TypeError, ValueError):
            display = str(pct)
        return f"pct-inflow: {display}"
    return str(strategy or "—")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_dict(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


__all__: list[str] = [
    "EnvelopeSummary",
    "EnvelopesData",
    "load_envelopes",
    "summarize_refill_rule",
]
