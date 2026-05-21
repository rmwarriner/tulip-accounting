"""Tag repository — CRUD for the normalised tag registry (ADR-0009, PR A).

Tags are household-scoped strings stored once in the ``tags`` table;
join tables (``transaction_tags`` today; ``posting_tags`` /
``account_tags`` in PR B) reference them by ``id``. The public API
of this repo still accepts and returns *names* — the int- vs string-
based identity of a tag is an implementation detail of the storage
layer, not something API callers should care about.

``get_or_create_by_name`` is the workhorse: it resolves a tag name to
its id, creating the row if missing. Idempotent and the right call
shape for "user passed me a tag string, give me an id I can put in
the join table."
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import Tag

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class TagRepository:
    """Per-household repository for the normalised tag registry."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repo to a session + tenant scope."""
        self._session = session
        self._household_id = household_id

    def get_or_create_by_name(self, name: str) -> Tag:
        """Resolve ``name`` to a :class:`Tag`, creating it if missing.

        ``name`` is taken as-given (already normalised by the caller).
        The household-scoped unique constraint on ``(household_id, name)``
        prevents duplicate rows on a race; on the happy path it's a
        single SELECT + (maybe) one INSERT.
        """
        existing = self._session.execute(
            select(Tag).where(
                Tag.household_id == self._household_id,
                Tag.name == name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        created = Tag(
            household_id=self._household_id,
            id=uuid4(),
            name=name,
        )
        self._session.add(created)
        self._session.flush()
        return created

    def get_by_name(self, name: str) -> Tag | None:
        """Return the tag named ``name``, or ``None``."""
        return self._session.execute(
            select(Tag).where(
                Tag.household_id == self._household_id,
                Tag.name == name,
            )
        ).scalar_one_or_none()

    def list_all(self) -> list[Tag]:
        """Return every tag in the household, ordered by name."""
        return list(
            self._session.execute(
                select(Tag).where(Tag.household_id == self._household_id).order_by(Tag.name)
            )
            .scalars()
            .all()
        )

    def rename(self, tag_id: UUID, new_name: str) -> Tag:
        """Rename a tag in place. O(1) by virtue of normalisation."""
        tag = self._session.execute(
            select(Tag).where(
                Tag.household_id == self._household_id,
                Tag.id == tag_id,
            )
        ).scalar_one()
        tag.name = new_name
        self._session.flush()
        return tag


__all__ = ["TagRepository"]
