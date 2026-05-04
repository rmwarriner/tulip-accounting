"""Transaction model — header for a balanced set of postings."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household

# P5.1 added five reconciliation/import-link columns; their FKs are wired
# at the migration layer via batch_alter_table. The mapped attributes here
# are nullable and untyped at the FK level (the migration declares the
# composite FKs to reconciliations / import_batches).


class TransactionStatus(Enum):
    """Workflow status — see tulip_core.transactions.TransactionStatus."""

    PENDING = "pending"
    POSTED = "posted"
    RECONCILED = "reconciled"


class Transaction(Base):
    """A double-entry transaction header. Postings live in `postings`."""

    __tablename__ = "transactions"

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, native_enum=False, length=20), nullable=False
    )
    notes_encrypted: Mapped[bytes | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    voided_by_transaction_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    imported_from_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    carried_forward_from_reconciliation_id: Mapped[UUID | None] = mapped_column(
        GUID(), nullable=True
    )
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
