"""Transaction-tag repository (ADR-0009).

Bridges the public string-passing API and the normalised id-keyed
``transaction_tags`` join table. Callers pass tag *names*; this
module resolves them to / from the ``tags`` table behind the
scenes.

Public surface (``set_tags``, ``list_tags``, ``list_tags_for_
transactions``, ``find_transaction_ids_by_tag``) is unchanged from
the #39 shape so the rest of the codebase doesn't need to learn
about tag ids.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from tulip_storage.models import Tag, TransactionTag
from tulip_storage.repositories.tag import TagRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class TagInvalidError(ValueError):
    """A tag string failed validation (empty, too long, illegal chars)."""


#: Tags are intentionally restrictive in v1: printable, non-whitespace
#: identifiers up to 64 chars. Whitespace-bearing tags would make
#: filter URL syntax ambiguous; control chars + null bytes have no
#: legitimate use. The grammar can widen in later slices (#39 follow-up).
_TAG_RE = re.compile(r"^[A-Za-z0-9_\-./:][A-Za-z0-9_\-./:]{0,63}$")


def normalise_tag(value: str) -> str:
    """Strip + lowercase a tag candidate; raise :class:`TagInvalidError` on bad input."""
    if not isinstance(value, str):
        raise TagInvalidError(f"tag must be a string, got {type(value).__name__}")
    stripped = value.strip().lower()
    if not stripped:
        raise TagInvalidError("tag cannot be empty")
    if len(stripped) > 64:
        raise TagInvalidError(f"tag {stripped!r} exceeds 64 characters")
    if not _TAG_RE.fullmatch(stripped):
        raise TagInvalidError(f"tag {stripped!r} must be 1-64 chars of [A-Z a-z 0-9 _ - . / :]")
    return stripped


class TransactionTagRepository:
    """Per-household repository over the ``transaction_tags`` edge table."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repo to a session + household tenant scope."""
        self._session = session
        self._household_id = household_id
        self._tags = TagRepository(session, household_id)

    def set_tags(self, transaction_id: UUID, tags: list[str]) -> list[str]:
        """Replace the transaction's tags with the given set (atomic per-call).

        Each name is normalised + deduplicated, then resolved to a
        :class:`Tag` row (created on demand). Returns the stored list
        in sorted order for deterministic round-trip.
        """
        normalised = sorted({normalise_tag(t) for t in tags})
        self._session.execute(
            delete(TransactionTag).where(
                TransactionTag.household_id == self._household_id,
                TransactionTag.transaction_id == transaction_id,
            )
        )
        for name in normalised:
            tag = self._tags.get_or_create_by_name(name)
            self._session.add(
                TransactionTag(
                    household_id=self._household_id,
                    transaction_id=transaction_id,
                    tag_id=tag.id,
                )
            )
        return normalised

    def list_tags(self, transaction_id: UUID) -> list[str]:
        """Return the transaction's tag *names* in sorted order."""
        rows = (
            self._session.execute(
                select(Tag.name)
                .join(
                    TransactionTag,
                    (TransactionTag.household_id == Tag.household_id)
                    & (TransactionTag.tag_id == Tag.id),
                )
                .where(
                    TransactionTag.household_id == self._household_id,
                    TransactionTag.transaction_id == transaction_id,
                )
                .order_by(Tag.name)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_tags_for_transactions(self, transaction_ids: list[UUID]) -> dict[UUID, list[str]]:
        """Batch lookup: return ``{tx_id: [tag, …]}`` for a list of transactions."""
        if not transaction_ids:
            return {}
        rows = self._session.execute(
            select(TransactionTag.transaction_id, Tag.name)
            .join(
                Tag,
                (Tag.household_id == TransactionTag.household_id)
                & (Tag.id == TransactionTag.tag_id),
            )
            .where(
                TransactionTag.household_id == self._household_id,
                TransactionTag.transaction_id.in_(transaction_ids),
            )
            .order_by(TransactionTag.transaction_id, Tag.name)
        ).all()
        by_tx: dict[UUID, list[str]] = {tx_id: [] for tx_id in transaction_ids}
        for tx_id, name in rows:
            by_tx[tx_id].append(name)
        return by_tx

    def find_transaction_ids_by_tag(self, tag: str) -> list[UUID]:
        """Return the household's transaction ids that carry ``tag``.

        Used by ``GET /v1/transactions?tag=foo``. The tag is normalised
        and resolved to its id, then joined against ``transaction_tags``.
        Unknown tags return an empty list.
        """
        normalised = normalise_tag(tag)
        existing = self._tags.get_by_name(normalised)
        if existing is None:
            return []
        rows = (
            self._session.execute(
                select(TransactionTag.transaction_id)
                .where(
                    TransactionTag.household_id == self._household_id,
                    TransactionTag.tag_id == existing.id,
                )
                .order_by(TransactionTag.transaction_id)
            )
            .scalars()
            .all()
        )
        return list(rows)


__all__: list[str] = [
    "TagInvalidError",
    "TransactionTagRepository",
    "normalise_tag",
]
