"""Posting-tag repository (ADR-0009, PR B).

Same shape as :class:`TransactionTagRepository` — public API
operates by tag name, resolves to :class:`Tag` ids via
:class:`TagRepository` internally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from tulip_storage.models import PostingTag, Tag
from tulip_storage.repositories.tag import TagRepository
from tulip_storage.repositories.transaction_tag import normalise_tag

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostingTagRepository:
    """Per-household repository over the ``posting_tags`` edge table."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repo to a session + household tenant scope."""
        self._session = session
        self._household_id = household_id
        self._tags = TagRepository(session, household_id)

    def set_tags(self, posting_id: UUID, tags: list[str]) -> list[str]:
        """Replace the posting's tags with the deduplicated, normalised set."""
        normalised = sorted({normalise_tag(t) for t in tags})
        self._session.execute(
            delete(PostingTag).where(
                PostingTag.household_id == self._household_id,
                PostingTag.posting_id == posting_id,
            )
        )
        for name in normalised:
            tag = self._tags.get_or_create_by_name(name)
            self._session.add(
                PostingTag(
                    household_id=self._household_id,
                    posting_id=posting_id,
                    tag_id=tag.id,
                )
            )
        return normalised

    def list_tags(self, posting_id: UUID) -> list[str]:
        """Return the posting's tag *names* in sorted order."""
        rows = (
            self._session.execute(
                select(Tag.name)
                .join(
                    PostingTag,
                    (PostingTag.household_id == Tag.household_id) & (PostingTag.tag_id == Tag.id),
                )
                .where(
                    PostingTag.household_id == self._household_id,
                    PostingTag.posting_id == posting_id,
                )
                .order_by(Tag.name)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_tags_for_postings(self, posting_ids: list[UUID]) -> dict[UUID, list[str]]:
        """Batch lookup: return ``{posting_id: [tag, ...]}`` for the list."""
        if not posting_ids:
            return {}
        rows = self._session.execute(
            select(PostingTag.posting_id, Tag.name)
            .join(
                Tag,
                (Tag.household_id == PostingTag.household_id) & (Tag.id == PostingTag.tag_id),
            )
            .where(
                PostingTag.household_id == self._household_id,
                PostingTag.posting_id.in_(posting_ids),
            )
            .order_by(PostingTag.posting_id, Tag.name)
        ).all()
        by_id: dict[UUID, list[str]] = {pid: [] for pid in posting_ids}
        for posting_id, name in rows:
            by_id[posting_id].append(name)
        return by_id

    def find_posting_ids_by_tag(self, tag: str) -> list[UUID]:
        """Return the postings carrying ``tag``. Unknown tag → empty list."""
        normalised = normalise_tag(tag)
        existing = self._tags.get_by_name(normalised)
        if existing is None:
            return []
        rows = (
            self._session.execute(
                select(PostingTag.posting_id)
                .where(
                    PostingTag.household_id == self._household_id,
                    PostingTag.tag_id == existing.id,
                )
                .order_by(PostingTag.posting_id)
            )
            .scalars()
            .all()
        )
        return list(rows)


__all__ = ["PostingTagRepository"]
