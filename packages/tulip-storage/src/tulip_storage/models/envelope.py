"""Envelope model — joined to AllocationPool via composite (household_id, pool_id) FK."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKeyConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.allocation_pool import AllocationPool
from tulip_storage.models.base import GUID, Base, SqliteDecimal


class BudgetPeriod(Enum):
    """Budget refresh cadence for an envelope."""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    CUSTOM = "custom"


class RolloverPolicy(Enum):
    """End-of-period rollover behavior for an envelope."""

    RESET = "reset"
    ACCUMULATE = "accumulate"
    CAP_AT_BUDGET = "cap_at_budget"


class Envelope(Base):
    """Envelope detail rows joined to allocation_pools."""

    __tablename__ = "envelopes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "pool_id"],
            ["allocation_pools.household_id", "allocation_pools.id"],
            ondelete="CASCADE",
            name="fk_envelopes_pool",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    pool_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    budget_period: Mapped[BudgetPeriod] = mapped_column(
        SAEnum(
            BudgetPeriod,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    budget_amount: Mapped[Decimal | None] = mapped_column(SqliteDecimal(20, 8), nullable=True)
    rollover_policy: Mapped[RolloverPolicy] = mapped_column(
        SAEnum(
            RolloverPolicy,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    refill_rule_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    pool: Mapped[AllocationPool] = relationship(
        primaryjoin=(
            "and_("
            "Envelope.household_id == AllocationPool.household_id, "
            "Envelope.pool_id == AllocationPool.id"
            ")"
        ),
        foreign_keys="[Envelope.household_id, Envelope.pool_id]",
        viewonly=True,
    )
