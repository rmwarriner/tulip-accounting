"""SinkingFundRepository — household-scoped CRUD for sinking funds.

Mirrors :class:`EnvelopeRepository`. Sinking funds are joined to
``allocation_pools`` by ``(household_id, pool_id)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import (
    AllocationPool,
    ContributionStrategy,
    PoolType,
    SinkingFund,
)

if TYPE_CHECKING:
    from datetime import date as date_type
    from decimal import Decimal

    from sqlalchemy.orm import Session


class SinkingFundRepository:
    """CRUD for sinking funds within a single household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        name: str,
        currency: str,
        target_amount: Decimal,
        target_date: date_type,
        contribution_strategy: ContributionStrategy,
        contribution_amount: Decimal | None = None,
        visibility: str = "shared",
        created_by_user_id: UUID | None = None,
    ) -> tuple[AllocationPool, SinkingFund]:
        """Insert the pool row and the joined sinking-fund row in one shot."""
        pool = AllocationPool(
            household_id=self._household_id,
            id=uuid4(),
            pool_type=PoolType.SINKING_FUND,
            name=name,
            currency=currency,
            visibility=visibility,
            is_active=True,
            is_system=False,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(pool)
        self._session.flush()

        sf = SinkingFund(
            household_id=self._household_id,
            pool_id=pool.id,
            target_amount=target_amount,
            target_date=target_date,
            contribution_strategy=contribution_strategy,
            contribution_amount=contribution_amount,
        )
        self._session.add(sf)
        self._session.flush()
        return pool, sf

    def get(self, pool_id: UUID) -> tuple[AllocationPool, SinkingFund] | None:
        """Return ``(pool, sinking_fund)`` for a sinking fund in this household, or None."""
        row = self._session.execute(
            select(AllocationPool, SinkingFund)
            .join(
                SinkingFund,
                (SinkingFund.household_id == AllocationPool.household_id)
                & (SinkingFund.pool_id == AllocationPool.id),
            )
            .where(
                AllocationPool.household_id == self._household_id,
                AllocationPool.id == pool_id,
                AllocationPool.pool_type == PoolType.SINKING_FUND,
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    def list_active(self) -> list[tuple[AllocationPool, SinkingFund]]:
        """Return all active sinking funds in this household, ordered by name."""
        rows = self._session.execute(
            select(AllocationPool, SinkingFund)
            .join(
                SinkingFund,
                (SinkingFund.household_id == AllocationPool.household_id)
                & (SinkingFund.pool_id == AllocationPool.id),
            )
            .where(
                AllocationPool.household_id == self._household_id,
                AllocationPool.pool_type == PoolType.SINKING_FUND,
                AllocationPool.is_active.is_(True),
            )
            .order_by(AllocationPool.name)
        ).all()
        return [(r[0], r[1]) for r in rows]

    def update_fields(
        self,
        pool_id: UUID,
        *,
        # Pool-row fields
        name: str | None = None,
        visibility: str | None = None,
        # Sinking-fund-row fields
        target_amount: Decimal | None = None,
        target_date: date_type | None = None,
        contribution_strategy: ContributionStrategy | None = None,
        contribution_amount: Decimal | None = None,
        clear_contribution_amount: bool = False,
    ) -> tuple[AllocationPool, SinkingFund]:
        """Update mutable fields on either the pool row or the sinking-fund row."""
        existing = self.get(pool_id)
        if existing is None:
            raise LookupError(f"sinking fund {pool_id} not found in household {self._household_id}")
        pool, sf = existing
        if name is not None:
            pool.name = name
        if visibility is not None:
            pool.visibility = visibility
        if target_amount is not None:
            sf.target_amount = target_amount
        if target_date is not None:
            sf.target_date = target_date
        if contribution_strategy is not None:
            sf.contribution_strategy = contribution_strategy
        if contribution_amount is not None:
            sf.contribution_amount = contribution_amount
        elif clear_contribution_amount:
            sf.contribution_amount = None
        self._session.flush()
        return pool, sf
