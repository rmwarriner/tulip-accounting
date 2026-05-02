"""Pool: an account in the shadow ledger.

A Pool is the polymorphic base for envelopes, sinking funds, and the three
system pools (``Inflow``, ``Unallocated``, ``Spent``). Per ADR-0001, pool
balances are *never* stored — they are derived from the sum of associated
:class:`ShadowPosting` rows. The Pool value object therefore carries identity
and metadata only, no balance field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from tulip_core.currency import Currency

if TYPE_CHECKING:
    from uuid import UUID


class PoolType(Enum):
    """Polymorphic discriminator for an :class:`AllocationPool` row.

    Five variants:

    - ``ENVELOPE`` / ``SINKING_FUND``: user-created, visible in the user's
      pool list.
    - ``INFLOW`` / ``UNALLOCATED`` / ``SPENT``: system pools auto-created
      per ``(household, currency)``. Plumbing for the ledger; not editable.
    """

    ENVELOPE = "envelope"
    SINKING_FUND = "sinking_fund"
    INFLOW = "inflow"
    UNALLOCATED = "unallocated"
    SPENT = "spent"


SYSTEM_POOL_TYPES: frozenset[PoolType] = frozenset(
    {PoolType.INFLOW, PoolType.UNALLOCATED, PoolType.SPENT}
)


@dataclass(frozen=True, slots=True, eq=False)
class Pool:
    """An account in the shadow ledger.

    Equality is by ``id`` only; mirrors :class:`tulip_core.account.Account`.
    """

    id: UUID
    household_id: UUID
    pool_type: PoolType
    name: str
    currency: str
    visibility: str = field(default="shared")
    is_active: bool = field(default=True)
    is_system: bool = field(default=False)

    def __post_init__(self) -> None:
        """Validate currency code and the system-pool flag invariant."""
        Currency.from_code(self.currency)
        if not self.name:
            raise ValueError("Pool name must be non-empty")
        if self.visibility not in ("shared", "private"):
            raise ValueError(
                f"Pool visibility must be 'shared' or 'private', got {self.visibility!r}"
            )
        # System-pool flag must agree with the type discriminator. Everywhere
        # else in the system trusts is_system as the boolean predicate, so we
        # reject inconsistent constructions at the seam.
        type_is_system = self.pool_type in SYSTEM_POOL_TYPES
        if type_is_system and not self.is_system:
            raise ValueError(f"Pool of type {self.pool_type.value!r} must have is_system=True")
        if not type_is_system and self.is_system:
            raise ValueError(f"Pool of type {self.pool_type.value!r} cannot have is_system=True")
        # System pools are global plumbing — visibility doesn't apply.
        if self.is_system and self.visibility != "shared":
            raise ValueError("System pools must have visibility='shared'")

    def __eq__(self, other: object) -> bool:
        """Two Pools are equal iff their ids match."""
        if not isinstance(other, Pool):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by id, consistent with equality."""
        return hash(self.id)
