"""EnvelopeRepository — household-scoped CRUD for envelopes.

Envelopes are joined to ``allocation_pools`` by ``(household_id, pool_id)``.
Creation inserts both rows in a single repo method so router code stays
simple. Deactivation goes through :class:`AllocationPoolRepository.deactivate`
since soft-delete is uniform across pool types.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import (
    AllocationPool,
    BudgetPeriod,
    Envelope,
    PoolType,
    RolloverPolicy,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from sqlalchemy.orm import Session


class EnvelopeRepository:
    """CRUD for envelopes within a single household.

    The repo abstracts the two-table structure (`allocation_pools` +
    `envelopes`) behind a single create/get/list/update interface. Reads
    return ``(AllocationPool, Envelope)`` tuples; updates accept keyword
    args for either table's mutable columns.
    """

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        name: str,
        currency: str,
        budget_period: BudgetPeriod,
        rollover_policy: RolloverPolicy,
        budget_amount: Decimal | None = None,
        refill_rule: dict[str, Any] | None = None,
        visibility: str = "shared",
        created_by_user_id: UUID | None = None,
    ) -> tuple[AllocationPool, Envelope]:
        """Insert the pool row and the joined envelope row in one shot."""
        pool = AllocationPool(
            household_id=self._household_id,
            id=uuid4(),
            pool_type=PoolType.ENVELOPE,
            name=name,
            currency=currency,
            visibility=visibility,
            is_active=True,
            is_system=False,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(pool)
        self._session.flush()

        env = Envelope(
            household_id=self._household_id,
            pool_id=pool.id,
            budget_period=budget_period,
            budget_amount=budget_amount,
            rollover_policy=rollover_policy,
            refill_rule_json=json.dumps(refill_rule) if refill_rule is not None else None,
        )
        self._session.add(env)
        self._session.flush()
        return pool, env

    def get(self, pool_id: UUID) -> tuple[AllocationPool, Envelope] | None:
        """Return ``(pool, envelope)`` for an envelope in this household, or None."""
        row = self._session.execute(
            select(AllocationPool, Envelope)
            .join(
                Envelope,
                (Envelope.household_id == AllocationPool.household_id)
                & (Envelope.pool_id == AllocationPool.id),
            )
            .where(
                AllocationPool.household_id == self._household_id,
                AllocationPool.id == pool_id,
                AllocationPool.pool_type == PoolType.ENVELOPE,
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    def list_active(self) -> list[tuple[AllocationPool, Envelope]]:
        """Return all active envelopes in this household, ordered by name."""
        rows = self._session.execute(
            select(AllocationPool, Envelope)
            .join(
                Envelope,
                (Envelope.household_id == AllocationPool.household_id)
                & (Envelope.pool_id == AllocationPool.id),
            )
            .where(
                AllocationPool.household_id == self._household_id,
                AllocationPool.pool_type == PoolType.ENVELOPE,
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
        # Envelope-row fields
        budget_period: BudgetPeriod | None = None,
        budget_amount: Decimal | None = None,
        rollover_policy: RolloverPolicy | None = None,
        refill_rule: dict[str, Any] | None = None,
        # Sentinel: pass ``True`` to clear an existing refill_rule (set to
        # JSON null). Without this we can't distinguish "leave alone" from
        # "remove" since ``refill_rule=None`` already means "leave alone."
        clear_refill_rule: bool = False,
    ) -> tuple[AllocationPool, Envelope]:
        """Update mutable fields on either the pool row or the envelope row.

        Returns the updated ``(pool, envelope)`` tuple. Any None argument
        leaves that field unchanged. Raises ``LookupError`` if the
        envelope doesn't exist.
        """
        existing = self.get(pool_id)
        if existing is None:
            raise LookupError(f"envelope {pool_id} not found in household {self._household_id}")
        pool, env = existing
        if name is not None:
            pool.name = name
        if visibility is not None:
            pool.visibility = visibility
        if budget_period is not None:
            env.budget_period = budget_period
        if budget_amount is not None:
            env.budget_amount = budget_amount
        if rollover_policy is not None:
            env.rollover_policy = rollover_policy
        if refill_rule is not None:
            env.refill_rule_json = json.dumps(refill_rule)
        elif clear_refill_rule:
            env.refill_rule_json = None
        self._session.flush()
        return pool, env
