"""Posting-tag edge (ADR-0009, PR B).

Links a single :class:`Posting` to a normalised :class:`Tag`.
The composite PK ``(household_id, posting_id, tag_id)`` keeps
the row scoped; the FK to ``postings.id`` is single-column
because :class:`Posting` itself has a single-column PK
(unlike ``transactions``/``accounts`` which use composite
PKs). Tenant isolation on the posting side is therefore an
application-layer concern (the repository filters by
``household_id``) — matching the pattern other single-PK
references use.

Tag-side FK stays composite — the tags table has the
composite PK already.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class PostingTag(Base):
    """Edge from a posting to a tag (ADR-0009)."""

    __tablename__ = "posting_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "tag_id"],
            ["tags.household_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_posting_tags_tag",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    posting_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("postings.id", ondelete="CASCADE", name="fk_posting_tags_posting"),
        primary_key=True,
    )
    tag_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
