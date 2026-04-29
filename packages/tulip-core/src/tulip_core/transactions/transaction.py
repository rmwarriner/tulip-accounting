"""Transaction: a balanced set of postings."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from tulip_core.transactions.posting import Posting


class TransactionStatus(Enum):
    """Workflow status for a transaction.

    PENDING: provisionally created (e.g. from import); may be unbalanced.
    POSTED: committed to the ledger; postings must balance per currency.
    RECONCILED: matched against an external statement; still balanced.
    """

    PENDING = "pending"
    POSTED = "posted"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class Transaction:
    """A double-entry transaction.

    Holds a tuple of Postings. The double-entry invariant — that postings
    sum to zero per currency — is enforced at construction time when status
    is POSTED or RECONCILED. PENDING transactions may be unbalanced (e.g.
    statement lines awaiting categorization).
    """

    id: UUID
    household_id: UUID
    date: date
    description: str
    postings: tuple[Posting, ...]
    status: TransactionStatus
    reference: str | None = field(default=None)
    created_by_user_id: UUID | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate posting count and balance (when status requires it)."""
        if len(self.postings) < 2:
            raise ValueError(
                f"transaction must have at least two postings, got {len(self.postings)}"
            )
        if self.status in (TransactionStatus.POSTED, TransactionStatus.RECONCILED):
            balances = self.balance_per_currency()
            unbalanced = {ccy: bal for ccy, bal in balances.items() if bal != 0}
            if unbalanced:
                raise ValueError(f"transaction does not balance per currency: {unbalanced}")

    def balance_per_currency(self) -> dict[str, Decimal]:
        """Return a {currency: net amount} dict over all postings."""
        sums: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for posting in self.postings:
            sums[posting.amount.currency] += posting.amount.amount
        return dict(sums)

    def is_balanced(self) -> bool:
        """Return True iff every currency's postings sum to zero."""
        return all(b == 0 for b in self.balance_per_currency().values())
