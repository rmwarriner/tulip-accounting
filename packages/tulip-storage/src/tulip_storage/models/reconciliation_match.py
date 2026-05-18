"""ReconciliationMatch model — M:N statement_lines <-> ledger transactions.

Per ADR-0004 §Q3. Supports 1:1, N:1, and 1:N matches. ``match_amount``
is the portion of the statement line covered by this match; the sum
across rows for one statement line must equal the line's amount.

Confidence + matcher_version + created_by_user_id encode provenance:
``confidence`` NULL = manual match; populated = matcher-produced.
The FK to ``transactions`` is ``ON DELETE RESTRICT`` (not CASCADE) —
voiding a matched tx must fail loudly until the match is rejected.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base, SqliteDecimal


class MatchConfidence(Enum):
    """Bucketed match confidence per ADR-0004 §Q2."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReconciliationMatch(Base):
    """One match row pairing a statement line to a ledger transaction."""

    __tablename__ = "reconciliation_matches"

    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    reconciliation_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    statement_line_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    ledger_transaction_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    match_amount: Mapped[Decimal] = mapped_column(SqliteDecimal(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    confidence: Mapped[MatchConfidence | None] = mapped_column(
        SAEnum(
            MatchConfidence,
            native_enum=False,
            length=10,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=True,
    )
    matcher_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        PrimaryKeyConstraint("household_id", "id", name="pk_reconciliation_matches"),
        ForeignKeyConstraint(
            ["household_id", "reconciliation_id"],
            ["reconciliations.household_id", "reconciliations.id"],
            ondelete="CASCADE",
            name="fk_reconciliation_matches_reconciliation",
        ),
        ForeignKeyConstraint(
            ["household_id", "statement_line_id"],
            ["statement_lines.household_id", "statement_lines.id"],
            ondelete="CASCADE",
            name="fk_reconciliation_matches_statement_line",
        ),
        ForeignKeyConstraint(
            ["household_id", "ledger_transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="RESTRICT",
            name="fk_reconciliation_matches_transaction",
        ),
    )
