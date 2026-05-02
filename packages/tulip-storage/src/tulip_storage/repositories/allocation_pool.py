"""AllocationPoolRepository — household-scoped CRUD over the allocation_pools table.

Includes the system-pool resolver: ``get_or_create_system_pools(currency)``
returns the household's ``Inflow`` / ``Unallocated`` / ``Spent`` pools for the
given currency, creating the row(s) if missing. Wired into household
creation by the API layer; called lazily by the shadow-ledger writer the
first time a new currency shows up in budgeting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import AllocationPool, PoolType

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_SYSTEM_POOL_TYPES: tuple[PoolType, ...] = (
    PoolType.INFLOW,
    PoolType.UNALLOCATED,
    PoolType.SPENT,
)

_SYSTEM_POOL_NAMES: dict[PoolType, str] = {
    PoolType.INFLOW: "Inflow",
    PoolType.UNALLOCATED: "Unallocated",
    PoolType.SPENT: "Spent",
}


class AllocationPoolRepository:
    """CRUD + system-pool resolution for one household's allocation_pools."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        pool_type: PoolType,
        name: str,
        currency: str,
        visibility: str = "shared",
        is_system: bool = False,
        created_by_user_id: UUID | None = None,
    ) -> AllocationPool:
        """Insert a new AllocationPool into this repository's household."""
        p = AllocationPool(
            household_id=self._household_id,
            id=uuid4(),
            pool_type=pool_type,
            name=name,
            currency=currency,
            visibility=visibility,
            is_active=True,
            is_system=is_system,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(p)
        self._session.flush()
        return p

    def get(self, pool_id: UUID) -> AllocationPool | None:
        """Return the AllocationPool with the given id, or None."""
        return self._session.execute(
            select(AllocationPool).where(
                AllocationPool.household_id == self._household_id,
                AllocationPool.id == pool_id,
            )
        ).scalar_one_or_none()

    def list_active(self) -> list[AllocationPool]:
        """Return all active pools in this household, including system pools."""
        return list(
            self._session.execute(
                select(AllocationPool)
                .where(
                    AllocationPool.household_id == self._household_id,
                    AllocationPool.is_active.is_(True),
                )
                .order_by(AllocationPool.is_system, AllocationPool.name)
            )
            .scalars()
            .all()
        )

    def deactivate(self, pool_id: UUID) -> AllocationPool:
        """Mark a pool inactive (soft delete). Raises if missing or system."""
        p = self.get(pool_id)
        if p is None:
            raise LookupError(f"pool {pool_id} not found in household {self._household_id}")
        if p.is_system:
            raise ValueError(f"system pool {pool_id} ({p.name!r}) cannot be deactivated")
        p.is_active = False
        self._session.flush()
        return p

    def get_system_pool(self, *, pool_type: PoolType, currency: str) -> AllocationPool | None:
        """Return the system pool for ``(household, pool_type, currency)``, or None."""
        if pool_type not in _SYSTEM_POOL_TYPES:
            raise ValueError(f"pool_type {pool_type.value!r} is not a system pool type")
        return self._session.execute(
            select(AllocationPool).where(
                AllocationPool.household_id == self._household_id,
                AllocationPool.pool_type == pool_type,
                AllocationPool.currency == currency,
                AllocationPool.is_system.is_(True),
            )
        ).scalar_one_or_none()

    def get_or_create_system_pools(self, *, currency: str) -> dict[PoolType, AllocationPool]:
        """Return the three system pools for ``(household, currency)``.

        Creates any rows that are missing. Idempotent: calling twice with
        the same currency returns the same rows. Used both eagerly at
        household creation and lazily by the shadow-ledger writer when a
        new currency first appears in budgeting.
        """
        out: dict[PoolType, AllocationPool] = {}
        for pool_type in _SYSTEM_POOL_TYPES:
            existing = self.get_system_pool(pool_type=pool_type, currency=currency)
            if existing is None:
                existing = self.create(
                    pool_type=pool_type,
                    name=f"{_SYSTEM_POOL_NAMES[pool_type]} {currency}",
                    currency=currency,
                    is_system=True,
                )
            out[pool_type] = existing
        return out
