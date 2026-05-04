"""AttachmentLink model — many-to-many between attachments and entities.

Polymorphic link table per ADR-0004 §"Schema (P5.1 migration sketch)".
``entity_type`` discriminates among ``transaction``, ``account``,
``reconciliation``, ``sinking_fund``, ``import_batch``; ``entity_id`` is
the UUID of the linked entity (no DB-level FK because the target table
varies — application-layer integrity).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base

_ALLOWED_ENTITY_TYPES = (
    "transaction",
    "account",
    "reconciliation",
    "sinking_fund",
    "import_batch",
)


class AttachmentLink(Base):
    """Polymorphic link from an attachment to a domain entity."""

    __tablename__ = "attachment_links"

    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "household_id",
            "attachment_id",
            "entity_type",
            "entity_id",
            name="pk_attachment_links",
        ),
        ForeignKeyConstraint(
            ["household_id", "attachment_id"],
            ["attachments.household_id", "attachments.id"],
            ondelete="CASCADE",
            name="fk_attachment_links_attachment",
        ),
        CheckConstraint(
            "entity_type IN (" + ", ".join(f"'{t}'" for t in _ALLOWED_ENTITY_TYPES) + ")",
            name="ck_attachment_links_entity_type",
        ),
    )
