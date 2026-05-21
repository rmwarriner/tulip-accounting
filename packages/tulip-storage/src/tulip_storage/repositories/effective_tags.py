"""Effective-tags resolver — ADR-0009 PR C (read-time inheritance).

Computes the inherited tag set for a posting or transaction by
unioning direct edges with the inherited ones:

- ``effective_tags(posting)`` = direct posting tags  |
  the parent transaction's direct tags  |
  the posting's account's direct tags.
- ``effective_tags(transaction)`` = direct transaction tags  |
  the union of every posting's direct tags  |
  the union of every posting's account's direct tags.

Inheritance is **never materialised** — it's a read-time UNION
across the three join tables and the ``tags`` registry. This
keeps tag renames + tag merges + account-tag re-edits O(1) on
write; the cost shows up only on the relatively-rare effective-
tags read. See the ADR for the rationale.

Each result is annotated with provenance so the caller can
distinguish direct vs inherited tags and show the user where an
inherited tag came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.sql.elements import ColumnElement

from tulip_storage.models import (
    AccountTag,
    Posting,
    PostingTag,
    Tag,
    TransactionTag,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


#: Where an effective tag came from. ``"posting"`` means a direct
#: edge in ``posting_tags``; ``"transaction"`` means a direct edge
#: on the parent transaction; ``"account"`` means a direct edge on
#: the account a posting points at.
TagProvenance = Literal["posting", "transaction", "account"]


@dataclass(frozen=True, slots=True)
class EffectiveTag:
    """One tag attached to a posting/transaction via direct or inherited edge.

    Multiple provenance entries can exist for the same ``name``
    (e.g. tag ``walter`` is on the posting AND on the parent
    transaction). The caller decides whether to dedupe by name or
    surface every provenance edge.
    """

    name: str
    provenance: TagProvenance
    source_id: UUID  # posting/transaction/account id that carries the direct edge


class EffectiveTagsRepository:
    """Per-household resolver for read-time tag inheritance (ADR-0009)."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the resolver to a session + household tenant scope."""
        self._session = session
        self._household_id = household_id

    def for_posting(self, posting_id: UUID) -> list[EffectiveTag]:
        """Return the full provenance-annotated effective tag list for ``posting_id``.

        Three branches:

        - Direct: rows in ``posting_tags`` for this posting.
        - From the parent transaction: rows in ``transaction_tags``
          for the posting's transaction_id.
        - From the posting's account: rows in ``account_tags`` for
          the posting's account_id.

        Sorted by (provenance, name) for deterministic output.
        """
        posting = self._session.execute(
            select(Posting).where(
                Posting.household_id == self._household_id,
                Posting.id == posting_id,
            )
        ).scalar_one_or_none()
        if posting is None:
            return []
        out: list[EffectiveTag] = []
        # Direct posting tags.
        for name in self._direct_names(PostingTag, PostingTag.posting_id == posting_id):
            out.append(EffectiveTag(name=name, provenance="posting", source_id=posting_id))
        # Inherited from the transaction.
        for name in self._direct_names(
            TransactionTag, TransactionTag.transaction_id == posting.transaction_id
        ):
            out.append(
                EffectiveTag(
                    name=name,
                    provenance="transaction",
                    source_id=posting.transaction_id,
                )
            )
        # Inherited from the account.
        for name in self._direct_names(AccountTag, AccountTag.account_id == posting.account_id):
            out.append(EffectiveTag(name=name, provenance="account", source_id=posting.account_id))
        out.sort(key=lambda t: (t.provenance, t.name))
        return out

    def for_transaction(self, transaction_id: UUID) -> list[EffectiveTag]:
        """Return the effective tag list for a transaction.

        - Direct transaction tags (one row per tag).
        - Direct posting tags on every posting under the transaction
          (one row per (posting, tag) edge so provenance can
          distinguish).
        - Direct account tags on every account a posting points at.

        Sorted by (provenance, name) for deterministic output.
        """
        out: list[EffectiveTag] = []
        # Direct transaction tags.
        for name in self._direct_names(
            TransactionTag, TransactionTag.transaction_id == transaction_id
        ):
            out.append(
                EffectiveTag(
                    name=name,
                    provenance="transaction",
                    source_id=transaction_id,
                )
            )
        # Posting tags on every posting under this transaction.
        posting_rows = (
            self._session.execute(
                select(Posting).where(
                    Posting.household_id == self._household_id,
                    Posting.transaction_id == transaction_id,
                )
            )
            .scalars()
            .all()
        )
        for posting in posting_rows:
            for name in self._direct_names(PostingTag, PostingTag.posting_id == posting.id):
                out.append(EffectiveTag(name=name, provenance="posting", source_id=posting.id))
            for name in self._direct_names(AccountTag, AccountTag.account_id == posting.account_id):
                out.append(
                    EffectiveTag(
                        name=name,
                        provenance="account",
                        source_id=posting.account_id,
                    )
                )
        out.sort(key=lambda t: (t.provenance, t.name))
        return out

    def _direct_names(
        self,
        edge_model: type[Any],
        where_clause: ColumnElement[bool],
    ) -> list[str]:
        """Return the tag names for a direct-edge query (single side)."""
        return list(
            self._session.execute(
                select(Tag.name)
                .join(
                    edge_model,
                    (edge_model.household_id == Tag.household_id) & (edge_model.tag_id == Tag.id),
                )
                .where(
                    edge_model.household_id == self._household_id,
                    where_clause,
                )
                .order_by(Tag.name)
            )
            .scalars()
            .all()
        )


__all__ = ["EffectiveTag", "EffectiveTagsRepository", "TagProvenance"]
