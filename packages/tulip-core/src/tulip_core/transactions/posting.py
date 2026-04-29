"""Posting: a single double-entry ledger line."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from tulip_core.money import Money


@dataclass(frozen=True, slots=True)
class Posting:
    """One side of a double-entry ledger line.

    Sign convention: positive amount = debit, negative = credit. The sum of
    all postings within a Transaction must be zero per currency for the
    transaction to be considered balanced.

    FX fields: when the posting's currency differs from the receiving
    account's native currency, callers supply both `fx_rate` and `fx_amount`
    (the converted figure in the account's currency). `fx_rate` and
    `fx_amount` must be either both present or both absent.
    """

    id: UUID
    account_id: UUID
    amount: Money
    pool_id: UUID | None = field(default=None)
    memo: str | None = field(default=None)
    fx_rate: Decimal | None = field(default=None)
    fx_amount: Money | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate FX field consistency."""
        if (self.fx_rate is None) != (self.fx_amount is None):
            raise ValueError("fx_rate and fx_amount must both be set or both be None")
        if self.fx_rate is not None and self.fx_rate <= 0:
            raise ValueError(f"fx_rate must be positive, got {self.fx_rate}")
        if self.fx_amount is not None and self.fx_amount.currency == self.amount.currency:
            raise ValueError(
                "fx_amount currency must differ from posting currency; "
                "FX is meaningless when amounts are in the same currency"
            )
