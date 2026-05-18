"""Transaction-tag model — v1 labels-only tags surface (#39).

A free-form string tag attached to a transaction. The composite PK
``(household_id, transaction_id, tag)`` gives us uniqueness for free
(no separate UniqueConstraint) and matches the indexing strategy in
the migration: per-tx tag lookups hit the PK prefix, per-household
tag-filter lookups hit ``ix_transaction_tags_household_tag``.

This is intentionally the smallest useful slice of #39. Out of scope
for v1: account / posting tags, cascade semantics, key=value pairs,
filter grammar beyond a single ``?tag=`` parameter.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class TransactionTag(Base):
    """One free-form tag attached to a transaction (#39, v1)."""

    __tablename__ = "transaction_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_transaction",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)
