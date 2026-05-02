"""AllocationPool model — the polymorphic base for envelopes / sinking funds / system pools.

See ADR-0001. Pool balances are derived from :mod:`shadow_postings`; this
table carries identity, type discriminator, currency, and is_active /
is_system flags.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class PoolType(Enum):
    """Polymorphic discriminator for an :class:`AllocationPool`.

    Mirrors :class:`tulip_core.allocation.PoolType` exactly. The two enums
    live in different layers (storage vs. domain) by convention; the values
    must remain identical so converters at the seam are trivial.
    """

    ENVELOPE = "envelope"
    SINKING_FUND = "sinking_fund"
    INFLOW = "inflow"
    UNALLOCATED = "unallocated"
    SPENT = "spent"


class AllocationPool(Base):
    """An account in the shadow ledger."""

    __tablename__ = "allocation_pools"

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    pool_type: Mapped[PoolType] = mapped_column(
        SAEnum(
            PoolType,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default="shared")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    household: Mapped[Household] = relationship()
