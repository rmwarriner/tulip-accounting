"""Repository for the v1 ``transaction_tags`` table (#39)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from tulip_storage.models import TransactionTag

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
    """Per-household repository for the v1 transaction_tags surface (#39)."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repo to a session + household tenant scope."""
        self._session = session
        self._household_id = household_id

    def set_tags(self, transaction_id: UUID, tags: list[str]) -> list[str]:
        """Replace the transaction's tags with the given set (atomic per-call).

        Each tag is normalised + deduplicated; the stored set is the
        deduplicated union of the input. Returns the stored list in
        sorted order for deterministic round-trip.
        """
        normalised = sorted({normalise_tag(t) for t in tags})
        self._session.execute(
            delete(TransactionTag).where(
                TransactionTag.household_id == self._household_id,
                TransactionTag.transaction_id == transaction_id,
            )
        )
        for tag in normalised:
            self._session.add(
                TransactionTag(
                    household_id=self._household_id,
                    transaction_id=transaction_id,
                    tag=tag,
                )
            )
        return normalised

    def list_tags(self, transaction_id: UUID) -> list[str]:
        """Return the transaction's tags in sorted order (alphabetical)."""
        rows = (
            self._session.execute(
                select(TransactionTag.tag)
                .where(
                    TransactionTag.household_id == self._household_id,
                    TransactionTag.transaction_id == transaction_id,
                )
                .order_by(TransactionTag.tag)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_tags_for_transactions(self, transaction_ids: list[UUID]) -> dict[UUID, list[str]]:
        """Batch lookup: return ``{tx_id: [tag, …]}`` for a list of transactions.

        Used by the list-endpoint render so we don't fire one query per
        row. Tags are sorted within each transaction.
        """
        if not transaction_ids:
            return {}
        rows = self._session.execute(
            select(TransactionTag.transaction_id, TransactionTag.tag)
            .where(
                TransactionTag.household_id == self._household_id,
                TransactionTag.transaction_id.in_(transaction_ids),
            )
            .order_by(TransactionTag.transaction_id, TransactionTag.tag)
        ).all()
        by_tx: dict[UUID, list[str]] = {tx_id: [] for tx_id in transaction_ids}
        for tx_id, tag in rows:
            by_tx[tx_id].append(tag)
        return by_tx

    def find_transaction_ids_by_tag(self, tag: str) -> list[UUID]:
        """Return the household's transaction ids that carry ``tag``.

        Used by ``GET /v1/transactions?tag=foo``. The tag is normalised
        before lookup so the URL filter is case-insensitive. Hits the
        ``ix_transaction_tags_household_tag`` index.
        """
        normalised = normalise_tag(tag)
        rows = (
            self._session.execute(
                select(TransactionTag.transaction_id)
                .where(
                    TransactionTag.household_id == self._household_id,
                    TransactionTag.tag == normalised,
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
