"""ShadowTransaction: a balanced set of shadow postings.

Mirrors :class:`tulip_core.transactions.Transaction` but operates on the
parallel ledger whose accounts are :class:`Pool` instances. The balance
invariant (sum-to-zero per currency) is enforced on construction when the
status is ``POSTED``; ``PENDING`` is allowed to be unbalanced for
in-progress shapes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from tulip_core.allocation.shadow_posting import ShadowPosting


class ShadowTxStatus(Enum):
    """Workflow status for a shadow transaction.

    Three states:

    - ``PENDING``: provisionally constructed; may be unbalanced.
    - ``POSTED``: committed to the shadow ledger; postings balance.
    - ``VOIDED``: reversed via the void/reversal mechanic landing in Phase 5.
    """

    PENDING = "pending"
    POSTED = "posted"
    VOIDED = "voided"


class ShadowTxReason(Enum):
    """Why this shadow transaction exists. See ADR-0001."""

    BUDGET_INFLOW = "budget_inflow"
    REFILL = "refill"
    SPEND = "spend"
    TRANSFER = "transfer"
    ROLLOVER = "rollover"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ShadowTransaction:
    """A double-entry transaction in the shadow ledger."""

    id: UUID
    household_id: UUID
    date: date
    description: str
    reason: ShadowTxReason
    postings: tuple[ShadowPosting, ...]
    status: ShadowTxStatus
    paired_main_tx_id: UUID | None = field(default=None)
    created_by_user_id: UUID | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate posting count and per-currency balance for POSTED status."""
        if len(self.postings) < 2:
            raise ValueError(
                f"shadow transaction must have at least two postings, got {len(self.postings)}"
            )
        if self.status is ShadowTxStatus.POSTED:
            balances = self.balance_per_currency()
            unbalanced = {ccy: bal for ccy, bal in balances.items() if bal != 0}
            if unbalanced:
                raise ValueError(f"shadow transaction does not balance per currency: {unbalanced}")

    def balance_per_currency(self) -> dict[str, Decimal]:
        """Return a {currency: net amount} dict over all postings."""
        sums: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for posting in self.postings:
            sums[posting.amount.currency] += posting.amount.amount
        return dict(sums)

    def is_balanced(self) -> bool:
        """Return True iff every currency's postings sum to zero."""
        return all(b == 0 for b in self.balance_per_currency().values())
