"""Accounts-screen data adapter.

Combines two API reads into one immutable value object:

* ``GET /v1/accounts`` — every active account visible to the caller.
  Returns rows even when they have no postings, which matters for the
  TUI: a brand-new account should still appear in the browser.
* ``GET /v1/reports/trial-balance`` — current per-account balance in
  each account's currency. Only contains rows for accounts that have at
  least one posting.

The join is by ``account_id``. Accounts with no trial-balance row keep
``balance = None`` so the screen can distinguish "zero" (a real
$0.00 ledger) from "never posted" (no data yet).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class CurrencyTotal:
    """Sum of balances within a single currency."""

    currency: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AccountSummary:
    """One row in the account browser."""

    id: str
    code: str | None
    name: str
    type: str
    currency: str
    balance: Decimal | None
    is_placeholder: bool = False  # #52


@dataclass(frozen=True, slots=True)
class AccountGroup:
    """Accounts sharing an account ``type`` (``asset`` / ``liability`` / …)."""

    type: str
    accounts: tuple[AccountSummary, ...]
    totals: tuple[CurrencyTotal, ...]


@dataclass(frozen=True, slots=True)
class AccountsData:
    """The full payload the accounts screen needs to render."""

    as_of: str
    accounts: tuple[AccountSummary, ...]
    groups: tuple[AccountGroup, ...]


def load_accounts(client: TulipClient) -> AccountsData:
    """Fetch + join ``/v1/accounts`` and ``/v1/reports/trial-balance``."""
    accounts_raw = client.get("/v1/accounts", authenticated=True).json()
    trial_raw = client.get("/v1/reports/trial-balance", authenticated=True).json()

    balances_by_id = {
        str(row["account_id"]): Decimal(str(row["balance"])) for row in trial_raw.get("rows", [])
    }

    summaries: list[AccountSummary] = []
    for raw in accounts_raw:
        account_id = str(raw["id"])
        summaries.append(
            AccountSummary(
                id=account_id,
                code=raw.get("code"),
                name=str(raw.get("name", "")),
                type=str(raw.get("type", "")),
                currency=str(raw.get("currency", "")),
                balance=balances_by_id.get(account_id),
                is_placeholder=bool(raw.get("is_placeholder", False)),
            )
        )

    return AccountsData(
        as_of=str(trial_raw.get("as_of", "")),
        accounts=tuple(summaries),
        groups=tuple(_group_by_type(summaries)),
    )


def _group_by_type(summaries: list[AccountSummary]) -> list[AccountGroup]:
    """Group summaries by ``type`` preserving first-seen order; sum per-currency."""
    bucketed: dict[str, list[AccountSummary]] = {}
    for summary in summaries:
        bucketed.setdefault(summary.type, []).append(summary)

    groups: list[AccountGroup] = []
    for atype, members in bucketed.items():
        per_currency: dict[str, Decimal] = {}
        for member in members:
            if member.balance is None:
                continue
            per_currency[member.currency] = (
                per_currency.get(member.currency, Decimal("0")) + member.balance
            )
        totals = tuple(CurrencyTotal(currency=cur, amount=amt) for cur, amt in per_currency.items())
        groups.append(
            AccountGroup(
                type=atype,
                accounts=tuple(members),
                totals=totals,
            )
        )
    return groups


__all__: list[str] = [
    "AccountGroup",
    "AccountSummary",
    "AccountsData",
    "CurrencyTotal",
    "load_accounts",
]
