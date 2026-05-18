"""Transaction register data adapter.

Combines ``GET /v1/transactions`` (the raw ledger rows) with
``GET /v1/accounts`` (UUID → human name) so the screen renders rows
like ``Trader Joe's · Checking → Groceries  -$67.21`` without doing
the join itself.

Filter parameters (``account_id`` / ``status`` / ``date_from`` /
``date_to`` / ``limit``) map 1:1 onto the API's query string and stay
omitted when the caller leaves them at their default of ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class PostingSummary:
    """One posting line of a transaction with its account label pre-resolved."""

    account_id: str
    account_label: str
    amount: Decimal
    currency: str
    memo: str | None


@dataclass(frozen=True, slots=True)
class TransactionSummary:
    """One row of the transaction register."""

    id: str
    date: str
    description: str
    reference: str | None
    notes: str | None
    status: str
    postings: tuple[PostingSummary, ...]
    amount_display: str


@dataclass(frozen=True, slots=True)
class TransactionsData:
    """The full payload the transactions screen needs."""

    transactions: tuple[TransactionSummary, ...]


def load_transactions(
    client: TulipClient,
    *,
    account_id: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> TransactionsData:
    """Fetch transactions + the account lookup table, then join in memory."""
    accounts_raw = client.get("/v1/accounts", authenticated=True).json()
    label_by_id = {str(a["id"]): _account_label(a) for a in accounts_raw}

    params: dict[str, str] = {}
    if account_id is not None:
        params["account_id"] = account_id
    if status is not None:
        params["status"] = status
    if date_from is not None:
        params["from"] = date_from
    if date_to is not None:
        params["to"] = date_to
    if limit is not None:
        params["limit"] = str(limit)

    tx_raw = client.get(
        "/v1/transactions",
        authenticated=True,
        params=params or None,
    ).json()

    summaries = tuple(_to_summary(row, label_by_id) for row in tx_raw)
    return TransactionsData(transactions=summaries)


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
) -> TransactionSummary:
    # The API returns a list of posting dicts; cast at the trust
    # boundary instead of validating each field (the OpenAPI contract
    # test already covers the schema).
    raw_postings = cast("list[dict[str, object]]", row.get("postings") or [])
    postings: list[PostingSummary] = []
    for posting in raw_postings:
        account_id = str(posting.get("account_id", ""))
        postings.append(
            PostingSummary(
                account_id=account_id,
                account_label=label_by_id.get(account_id, "—"),
                amount=Decimal(str(posting.get("amount", "0"))),
                currency=str(posting.get("currency", "")),
                memo=_optional_str(posting.get("memo")),
            )
        )
    return TransactionSummary(
        id=str(row.get("id", "")),
        date=str(row.get("date", "")),
        description=str(row.get("description", "")),
        reference=_optional_str(row.get("reference")),
        notes=_optional_str(row.get("notes")),
        status=str(row.get("status", "")),
        postings=tuple(postings),
        amount_display=_amount_display(postings),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _amount_display(postings: list[PostingSummary]) -> str:
    """One-line summary for the table — the net spend-side amount with sign.

    Most transactions are two-posting: pick the negative leg (the
    account that *paid*) and render it as the headline. Multi-currency
    or multi-leg transactions fall back to the first posting's amount.
    """
    if not postings:
        return ""
    negatives = [p for p in postings if p.amount < 0]
    chosen = negatives[0] if negatives else postings[0]
    quantised = chosen.amount.quantize(Decimal("0.01"))
    return f"{quantised:,.2f} {chosen.currency}"


__all__: list[str] = [
    "PostingSummary",
    "TransactionSummary",
    "TransactionsData",
    "load_transactions",
]
