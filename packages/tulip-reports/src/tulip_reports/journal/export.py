"""Export the ledger as a hledger-compatible journal file (P7.4).

Format spec (subset of the full hledger language we emit):

    ; comment lines start with semicolons
    YYYY-MM-DD description
        account:name        amount currency
        account:name        amount currency

Rules we honour:

- One ``YYYY-MM-DD description`` header per transaction.
- Two-space indent before each posting; account name + amount
  separated by at least two spaces.
- Amounts use ``.`` as decimal point; no thousand separators (hledger
  accepts both, but ``.`` is canonical and simpler to parse).
- Account names use the colon hierarchy ``<type>:<code>:<name>`` so
  the tree visualisation in hledger / ledger-cli matches our
  in-app accounts tree. Accounts without a code fall back to
  ``<type>:<name>``.
- One blank line between transactions.

Excludes pending transactions — only POSTED + RECONCILED appear in
the export, mirroring the trial-balance / income-statement
conventions.

Voided transactions are also excluded (the void-and-replace pattern
from P5.0 means the reversal pair sums to zero on the books anyway;
exporting both halves would be technically correct but very noisy).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _account_path(account_code: str | None, account_name: str, account_type: str) -> str:
    """Build the hledger colon-hierarchy account path for one tulip account.

    ``Assets:1100:Checking`` if a code is set; ``Assets:Checking``
    otherwise. The hledger convention is title-case for the top-level
    type — we capitalise it. Account names are kept verbatim so the
    user's chosen labels survive the round-trip.
    """
    type_title = account_type.title()
    if account_code:
        return f"{type_title}:{account_code}:{account_name}"
    return f"{type_title}:{account_name}"


def _format_amount(amount: Decimal, currency: str) -> str:
    """Format ``amount`` as hledger's canonical ``<value> <currency>`` form.

    Banker's rounding to the currency's natural minor-unit precision —
    USD/EUR → 2, JPY → 0, BHD → 3 — via :meth:`Money.quantize_to_currency`
    so the journal export matches the rest of the Tulip rendering surfaces
    (issue #213). Hledger accepts arbitrary precision but the currency-
    natural representation matches what users see in the UI. Unknown
    currencies fall back to two decimals.
    """
    from tulip_core.money import Money

    try:
        quantized = Money(amount, currency).quantize_to_currency().amount
    except (ArithmeticError, ValueError):
        quantized = amount.quantize(Decimal("0.01"))
    return f"{quantized} {currency}"


def export_journal(
    session: Session,
    *,
    household_id: UUID,
    start: date_type | None = None,
    end: date_type | None = None,
    visible_account_filter: Callable[[str, UUID | None], bool] | None = None,
    include_metadata: bool = True,
) -> bytes:
    """Render the household's posted transactions as a hledger journal.

    Filter:
    - ``start`` / ``end`` bound the transaction date range (inclusive).
    - Pending + voided transactions are always excluded.
    - ``visible_account_filter(visibility, created_by) → bool`` drops
      postings on accounts the caller can't see, and skips transactions
      whose every posting becomes invisible. The router supplies a
      closure over the request's claims; tests can pass ``None``
      to see every account (#229).

    Privacy:
    - ``include_metadata`` (default True) controls whether the export's
      header comments carry the household name + tulip provenance. Set
      False (privacy audit L-5 / L-17, #351) when the bytes are headed
      to a tax preparer / accountant who doesn't need the household
      identity surfaced in the file. The transactions themselves are
      unchanged either way; only the leading comment block is muted.
    """
    from sqlalchemy import select

    from tulip_storage.models import (
        Account,
        Household,
        Posting,
        Transaction,
        TransactionStatus,
    )

    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    # Accounts lookup so we can build the colon-path per posting.
    accounts = {
        a.id: a
        for a in session.execute(select(Account).where(Account.household_id == household_id))
        .scalars()
        .all()
    }

    tx_query = (
        select(Transaction)
        .where(
            Transaction.household_id == household_id,
            Transaction.status.in_((TransactionStatus.POSTED, TransactionStatus.RECONCILED)),
            Transaction.voided_by_transaction_id.is_(None),
        )
        .order_by(Transaction.date, Transaction.id)
    )
    if start is not None:
        tx_query = tx_query.where(Transaction.date >= start)
    if end is not None:
        tx_query = tx_query.where(Transaction.date <= end)

    transactions = session.execute(tx_query).scalars().all()

    lines: list[str] = []
    if include_metadata:
        lines.append("; Tulip Accounting — hledger journal export")
        lines.append(f"; household: {household.name}")
        if start is not None:
            lines.append(f"; from: {start.isoformat()}")
        if end is not None:
            lines.append(f"; to: {end.isoformat()}")
        lines.append("")

    for tx in transactions:
        # Postings: stable order by amount sign (debits first, then credits)
        # so the output reads naturally.
        postings = (
            session.execute(
                select(Posting)
                .where(
                    Posting.household_id == household_id,
                    Posting.transaction_id == tx.id,
                )
                .order_by(Posting.amount.desc(), Posting.id)
            )
            .scalars()
            .all()
        )

        # Apply visibility filter at posting level. If every posting on a
        # transaction is invisible to the caller, skip the whole tx — its
        # description and amounts would all be derived from invisible
        # data (#229).
        if visible_account_filter is not None:
            visible_postings = []
            for p in postings:
                a = accounts.get(p.account_id)
                if a is None:
                    # Orphaned posting — treat as invisible defensively.
                    continue
                if visible_account_filter(a.visibility, a.created_by_user_id):
                    visible_postings.append(p)
            if not visible_postings:
                continue
            postings = visible_postings

        # Header line: date + description.
        description = tx.description.replace("\n", " ").strip() or "(no description)"
        if tx.reference:
            description = f"({tx.reference}) {description}"
        lines.append(f"{tx.date.isoformat()} {description}")

        for posting in postings:
            account = accounts.get(posting.account_id)
            if account is None:
                # Defensive: posting references an account that no
                # longer exists. Render with a placeholder name so the
                # journal stays parseable.
                path = f"Unknown:{posting.account_id}"
            else:
                path = _account_path(account.code, account.name, account.type.value)
            amount_str = _format_amount(Decimal(str(posting.amount)), posting.currency)
            # Two-space indent + at least two spaces between account
            # name and amount (hledger's required minimum separator).
            lines.append(f"    {path}  {amount_str}")

        lines.append("")  # blank line between transactions

    return ("\n".join(lines) + "\n").encode("utf-8")


__all__ = ["export_journal"]
