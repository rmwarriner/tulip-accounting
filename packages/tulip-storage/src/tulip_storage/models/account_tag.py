"""Account-tag edge (ADR-0009, PR B).

Links a single :class:`Account` to a normalised :class:`Tag`.
Account tags inherit down to every posting on that account at
read-time — see ``effective_tags`` (PR C). Composite FKs keep
tenant isolation tight; ON DELETE CASCADE on both sides means
removing the account or the tag sweeps the edge.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class AccountTag(Base):
    """Edge from an account to a tag (ADR-0009)."""

    __tablename__ = "account_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            ondelete="CASCADE",
            name="fk_account_tags_account",
        ),
        ForeignKeyConstraint(
            ["household_id", "tag_id"],
            ["tags.household_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_account_tags_tag",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    tag_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
