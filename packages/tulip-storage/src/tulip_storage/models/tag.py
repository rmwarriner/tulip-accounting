"""Normalised tag registry (ADR-0009, #447).

Replaces the freeform-string tag model from #39. A ``Tag`` is a
household-scoped row identified by ``id``; the tag's display name
lives in ``name`` and is unique within the household. Join tables
(``transaction_tags``, ``posting_tags``, ``account_tags``)
reference tags by ``id`` so a tag rename is O(1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class Tag(Base):
    """One household-scoped tag (ADR-0009, PR A)."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_tags_household_name"),)

    household_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("households.id", ondelete="CASCADE"),
        primary_key=True,
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
