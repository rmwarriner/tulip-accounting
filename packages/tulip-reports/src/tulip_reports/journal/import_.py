"""hledger journal import side (P7.5).

Takes ``ParsedJournal`` from :mod:`tulip_reports.journal.parse` and
resolves each posting's account path to a tulip account ID. Account
resolution rules:

1. Strip the leading ``<Type>:`` token and try matching the remaining
   path as ``<code>:<name>`` first (export's preferred shape).
2. Fall back to matching by exact account name within the matched
   type.
3. Unmappable paths surface as ``ImportError`` entries with line
   numbers so the user can fix the journal or create the missing
   account before retrying.

The import itself is "build a list of pending transactions" — the
actual database write happens through the existing
``TransactionRepository.create`` (and lands in PENDING status, never
POSTED, so the user reviews before promoting). That keeps the import
flow consistent with the existing OFX / QIF / CSV importers (#74).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_reports.journal.parse import ParsedJournal


@dataclass(frozen=True, slots=True)
class ResolvedPosting:
    """One posting with its account_path resolved to a tulip account_id."""

    account_id: UUID
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class ResolvedTransaction:
    """One transaction with all postings resolved + ready to insert."""

    date: date_type
    description: str
    reference: str | None
    postings: list[ResolvedPosting]


@dataclass(frozen=True, slots=True)
class ImportError_:
    """One import-side error: an account couldn't be mapped, or postings unbalanced."""

    line_number: int
    message: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    """The full resolve+validate output for one parsed journal."""

    transactions: list[ResolvedTransaction]
    errors: list[ImportError_] = field(default_factory=list)


def _resolve_account_path(
    accounts_by_code: Mapping[str, object],
    accounts_by_type_name: Mapping[tuple[str, str], object],
    path: str,
) -> tuple[UUID, str] | None:
    """Resolve a hledger account path back to a tulip account.

    Returns ``(account_id, currency)`` on success, ``None`` on failure.
    The currency is returned because the account model has a single
    declared currency that must match the posting's currency.
    """
    # Format: "<Type>:<code>:<name>" or "<Type>:<name>".
    parts = path.split(":", 2)
    if len(parts) < 2:
        return None
    type_token = parts[0].lower()  # "Expense" -> "expense"
    rest = ":".join(parts[1:])  # "5100:Food" or "Food"

    # Try by code first if the rest starts with a numeric-looking token.
    code_candidate = rest.split(":", 1)[0]
    if code_candidate in accounts_by_code:
        acct = accounts_by_code[code_candidate]
        # Type must match too — different households can reuse code values.
        if getattr(acct.type, "value", str(acct.type)) == type_token:  # type: ignore[attr-defined]
            return acct.id, acct.currency  # type: ignore[attr-defined]

    # Fall back to exact (type, name) match.
    # If `rest` looked like "code:name", take the name portion.
    name_candidate = rest.split(":", 1)[-1] if ":" in rest else rest
    acct2 = accounts_by_type_name.get((type_token, name_candidate))
    if acct2 is not None:
        return acct2.id, acct2.currency  # type: ignore[attr-defined]

    return None


def resolve_journal(
    session: Session,
    *,
    household_id: UUID,
    parsed: ParsedJournal,
) -> ImportResult:
    """Resolve parsed account paths to tulip accounts; validate balance.

    Returns ``ImportResult.transactions`` (the resolved-and-balanced
    transactions ready for insertion) and ``ImportResult.errors``
    (per-tx or per-posting errors). The caller decides whether to
    proceed if any errors are present.

    Validation:
    - Every posting's account_path must resolve.
    - Posting currency must match account currency.
    - Postings must sum to zero per currency (balance invariant).
    """
    from sqlalchemy import select

    from tulip_storage.models import Account

    accounts = (
        session.execute(select(Account).where(Account.household_id == household_id)).scalars().all()
    )
    by_code: dict[str, Account] = {a.code: a for a in accounts if a.code is not None}
    by_type_name: dict[tuple[str, str], Account] = {(a.type.value, a.name): a for a in accounts}

    resolved_txs: list[ResolvedTransaction] = []
    errors: list[ImportError_] = []

    for tx in parsed.transactions:
        resolved_postings: list[ResolvedPosting] = []
        tx_errors: list[ImportError_] = []

        for posting in tx.postings:
            match = _resolve_account_path(by_code, by_type_name, posting.account_path)
            if match is None:
                tx_errors.append(
                    ImportError_(
                        line_number=posting.line_number,
                        message=(f"could not resolve account path {posting.account_path!r}"),
                    )
                )
                continue
            account_id, account_currency = match
            if account_currency != posting.currency:
                tx_errors.append(
                    ImportError_(
                        line_number=posting.line_number,
                        message=(
                            f"posting currency {posting.currency!r} does not match "
                            f"account currency {account_currency!r}"
                        ),
                    )
                )
                continue
            resolved_postings.append(
                ResolvedPosting(
                    account_id=account_id,
                    amount=posting.amount,
                    currency=posting.currency,
                )
            )

        if tx_errors:
            errors.extend(tx_errors)
            continue

        # Balance check: per-currency posting sum must be zero.
        sums_by_currency: dict[str, Decimal] = {}
        for p in resolved_postings:
            sums_by_currency[p.currency] = sums_by_currency.get(p.currency, Decimal("0")) + p.amount
        unbalanced = {c: s for c, s in sums_by_currency.items() if s != 0}
        if unbalanced:
            errors.append(
                ImportError_(
                    line_number=tx.line_number,
                    message=(
                        "postings do not balance: "
                        + ", ".join(f"{c}={s}" for c, s in unbalanced.items())
                    ),
                )
            )
            continue

        resolved_txs.append(
            ResolvedTransaction(
                date=tx.date,
                description=tx.description,
                reference=tx.reference,
                postings=resolved_postings,
            )
        )

    return ImportResult(transactions=resolved_txs, errors=errors)


__all__ = [
    "ImportError_",
    "ImportResult",
    "ResolvedPosting",
    "ResolvedTransaction",
    "resolve_journal",
]
