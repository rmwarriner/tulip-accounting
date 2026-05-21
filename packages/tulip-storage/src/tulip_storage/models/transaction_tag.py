"""Transaction-tag edge — links a transaction to a normalised Tag (ADR-0009).

Refactored from the freeform string surface of #39: the ``tag``
column is now a foreign key into ``tags(id)``, household-scoped via
the composite ``(household_id, tag_id)`` FK. A tag rename becomes
``UPDATE tags`` of one row instead of every transaction_tags row.

See [`docs/adrs/0009-tag-redesign.md`](../../../docs/adrs/0009-tag-redesign.md)
for the surrounding scope (also covers posting + account tags and
the read-time inheritance model that ships in PRs B and C).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class TransactionTag(Base):
    """Edge from a transaction to a normalised :class:`Tag` (ADR-0009)."""

    __tablename__ = "transaction_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_transaction",
        ),
        ForeignKeyConstraint(
            ["household_id", "tag_id"],
            ["tags.household_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_tag",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    tag_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
