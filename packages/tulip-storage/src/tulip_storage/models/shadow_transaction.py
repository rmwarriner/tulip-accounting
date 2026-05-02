"""ShadowTransaction model — header for a balanced set of shadow postings."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class ShadowTxStatus(Enum):
    """Workflow status — see tulip_core.allocation.ShadowTxStatus."""

    PENDING = "pending"
    POSTED = "posted"
    VOIDED = "voided"


class ShadowTxReason(Enum):
    """Why this shadow tx exists. See ADR-0001."""

    BUDGET_INFLOW = "budget_inflow"
    REFILL = "refill"
    SPEND = "spend"
    TRANSFER = "transfer"
    ROLLOVER = "rollover"
    MANUAL = "manual"


class ShadowTransaction(Base):
    """A shadow-ledger transaction header. Postings live in `shadow_postings`."""

    __tablename__ = "shadow_transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "paired_main_tx_id"],
            ["transactions.household_id", "transactions.id"],
            name="fk_shadow_transactions_paired_main_tx",
        ),
        ForeignKeyConstraint(
            ["household_id", "voided_by_shadow_tx_id"],
            ["shadow_transactions.household_id", "shadow_transactions.id"],
            name="fk_shadow_transactions_voided_by",
            use_alter=True,
        ),
    )

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[ShadowTxReason] = mapped_column(
        SAEnum(
            ShadowTxReason,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[ShadowTxStatus] = mapped_column(
        SAEnum(
            ShadowTxStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    paired_main_tx_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    voided_by_shadow_tx_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
