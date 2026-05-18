"""Reconciliation model — one "user closed statement PERIOD for ACCOUNT" event.

Per ADR-0004 §Q7. Reconciliation is a separate aggregate (this row is the
audit truth); ``transactions.reconciliation_id`` and ``transactions.reconciled_at``
are denormalizations populated by ``ReconciliationRepository.complete()``.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base, SqliteDecimal


class ReconciliationStatus(Enum):
    """Lifecycle status of a reconciliation."""

    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    ABANDONED = "abandoned"


class Reconciliation(Base):
    """Audit aggregate for a statement-vs-ledger reconciliation event."""

    __tablename__ = "reconciliations"

    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    account_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    statement_period_start: Mapped[date_type] = mapped_column(Date, nullable=False)
    statement_period_end: Mapped[date_type] = mapped_column(Date, nullable=False)
    statement_starting_balance: Mapped[Decimal] = mapped_column(
        SqliteDecimal(20, 8), nullable=False
    )
    statement_ending_balance: Mapped[Decimal] = mapped_column(SqliteDecimal(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[ReconciliationStatus] = mapped_column(
        SAEnum(
            ReconciliationStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    source_import_batch_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("household_id", "id", name="pk_reconciliations"),
        ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            name="fk_reconciliations_account",
        ),
        ForeignKeyConstraint(
            ["household_id", "source_import_batch_id"],
            ["import_batches.household_id", "import_batches.id"],
            name="fk_reconciliations_import_batch",
        ),
    )
