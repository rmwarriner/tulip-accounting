"""Account-tag repository (ADR-0009, PR B)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from tulip_storage.models import AccountTag, Tag
from tulip_storage.repositories.tag import TagRepository
from tulip_storage.repositories.transaction_tag import normalise_tag

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AccountTagRepository:
    """Per-household repository over the ``account_tags`` edge table."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repo to a session + household tenant scope."""
        self._session = session
        self._household_id = household_id
        self._tags = TagRepository(session, household_id)

    def set_tags(self, account_id: UUID, tags: list[str]) -> list[str]:
        """Replace the account's tags with the deduplicated, normalised set."""
        normalised = sorted({normalise_tag(t) for t in tags})
        self._session.execute(
            delete(AccountTag).where(
                AccountTag.household_id == self._household_id,
                AccountTag.account_id == account_id,
            )
        )
        for name in normalised:
            tag = self._tags.get_or_create_by_name(name)
            self._session.add(
                AccountTag(
                    household_id=self._household_id,
                    account_id=account_id,
                    tag_id=tag.id,
                )
            )
        return normalised

    def list_tags(self, account_id: UUID) -> list[str]:
        """Return the account's tag *names* in sorted order."""
        rows = (
            self._session.execute(
                select(Tag.name)
                .join(
                    AccountTag,
                    (AccountTag.household_id == Tag.household_id) & (AccountTag.tag_id == Tag.id),
                )
                .where(
                    AccountTag.household_id == self._household_id,
                    AccountTag.account_id == account_id,
                )
                .order_by(Tag.name)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_tags_for_accounts(self, account_ids: list[UUID]) -> dict[UUID, list[str]]:
        """Batch lookup: return ``{account_id: [tag, ...]}`` for the list."""
        if not account_ids:
            return {}
        rows = self._session.execute(
            select(AccountTag.account_id, Tag.name)
            .join(
                Tag,
                (Tag.household_id == AccountTag.household_id) & (Tag.id == AccountTag.tag_id),
            )
            .where(
                AccountTag.household_id == self._household_id,
                AccountTag.account_id.in_(account_ids),
            )
            .order_by(AccountTag.account_id, Tag.name)
        ).all()
        by_id: dict[UUID, list[str]] = {aid: [] for aid in account_ids}
        for account_id, name in rows:
            by_id[account_id].append(name)
        return by_id

    def find_account_ids_by_tag(self, tag: str) -> list[UUID]:
        """Return the accounts carrying ``tag``. Unknown tag → empty list."""
        normalised = normalise_tag(tag)
        existing = self._tags.get_by_name(normalised)
        if existing is None:
            return []
        rows = (
            self._session.execute(
                select(AccountTag.account_id)
                .where(
                    AccountTag.household_id == self._household_id,
                    AccountTag.tag_id == existing.id,
                )
                .order_by(AccountTag.account_id)
            )
            .scalars()
            .all()
        )
        return list(rows)


__all__ = ["AccountTagRepository"]
