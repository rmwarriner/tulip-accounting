"""CsvProfile model — per-household CSV column-mapping profile (P5.1).

Per ADR-0004 §Q8 (DB-only after the user picked single-storage). YAML
is the export / import format (``tulip imports profiles export | import``);
the canonical store is this table.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class CsvProfile(Base):
    """One named CSV column-mapping profile, scoped to a household."""

    __tablename__ = "csv_profiles"
    __table_args__ = (
        Index(
            "ix_csv_profiles_name",
            "household_id",
            "name",
            unique=True,
        ),
    )

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    yaml_body: Mapped[str] = mapped_column(Text, nullable=False)
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
