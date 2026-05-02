"""ShadowPosting: one leg of a shadow-ledger transaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from tulip_core.money import Money


@dataclass(frozen=True, slots=True)
class ShadowPosting:
    """One leg of a :class:`ShadowTransaction`.

    Sign convention mirrors the main ledger's: positive amount = into the
    pool, negative = out of the pool. The sum of every posting in a shadow
    transaction must be zero per currency for the transaction to balance.
    """

    id: UUID
    pool_id: UUID
    amount: Money
    memo: str | None = field(default=None)
