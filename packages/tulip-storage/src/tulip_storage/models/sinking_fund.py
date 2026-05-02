"""SinkingFund model — joined to AllocationPool via composite (household_id, pool_id) FK."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, ForeignKeyConstraint, Numeric
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.allocation_pool import AllocationPool
from tulip_storage.models.base import GUID, Base


class ContributionStrategy(Enum):
    """How a sinking fund's per-period contribution is computed."""

    MANUAL = "manual"
    EVEN_SPLIT = "even_split"
    PERCENTAGE_OF_INCOME = "percentage_of_income"


class SinkingFund(Base):
    """Sinking-fund detail rows joined to allocation_pools."""

    __tablename__ = "sinking_funds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "pool_id"],
            ["allocation_pools.household_id", "allocation_pools.id"],
            ondelete="CASCADE",
            name="fk_sinking_funds_pool",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    pool_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    target_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    contribution_strategy: Mapped[ContributionStrategy] = mapped_column(
        SAEnum(
            ContributionStrategy,
            native_enum=False,
            length=30,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    contribution_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    pool: Mapped[AllocationPool] = relationship(
        primaryjoin=(
            "and_("
            "SinkingFund.household_id == AllocationPool.household_id, "
            "SinkingFund.pool_id == AllocationPool.id"
            ")"
        ),
        foreign_keys="[SinkingFund.household_id, SinkingFund.pool_id]",
        viewonly=True,
    )
