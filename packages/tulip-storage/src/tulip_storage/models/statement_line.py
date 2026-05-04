"""StatementLine model — parsed bank-statement row in an import batch (P5.1).

Per ADR-0004 §Q8. ``fitid`` is OFX-specific (stable cross-statement
identifier); NULL for QIF / CSV. ``raw_json`` carries any format-specific
fields not in the common-denominator schema, for audit trail.

The matcher (P5.3) reads these rows; ``reconciliation_match_id`` is set
when a row gets matched, and a partial index on the unmatched rows feeds
the reconciliation inbox UI.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class StatementLine(Base):
    """One row from a bank statement, normalized into the common schema."""

    __tablename__ = "statement_lines"

    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    import_batch_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    posted_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fitid: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reconciliation_match_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("household_id", "id", name="pk_statement_lines"),
        ForeignKeyConstraint(
            ["household_id", "import_batch_id"],
            ["import_batches.household_id", "import_batches.id"],
            ondelete="CASCADE",
            name="fk_statement_lines_import_batch",
        ),
    )
