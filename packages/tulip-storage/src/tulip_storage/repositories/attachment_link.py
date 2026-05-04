"""AttachmentLinkRepository — polymorphic links from attachments to entities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from tulip_storage.models import AttachmentLink

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AttachmentLinkRepository:
    """Persists attachment-to-entity links within one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def list_for_entity(self, *, entity_type: str, entity_id: UUID) -> list[AttachmentLink]:
        """Return all attachment links for a given entity."""
        return list(
            self._session.execute(
                select(AttachmentLink).where(
                    AttachmentLink.household_id == self._household_id,
                    AttachmentLink.entity_type == entity_type,
                    AttachmentLink.entity_id == entity_id,
                )
            )
            .scalars()
            .all()
        )

    def link(self, *, attachment_id: UUID, entity_type: str, entity_id: UUID) -> AttachmentLink:
        """Create a link from an attachment to an entity. Idempotent."""
        existing = self._session.get(
            AttachmentLink,
            (self._household_id, attachment_id, entity_type, entity_id),
        )
        if existing is not None:
            return existing
        link = AttachmentLink(
            household_id=self._household_id,
            attachment_id=attachment_id,
            entity_type=entity_type,
            entity_id=entity_id,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(link)
        self._session.flush()
        return link

    def unlink(self, *, attachment_id: UUID, entity_type: str, entity_id: UUID) -> None:
        """Remove a specific attachment-to-entity link."""
        self._session.execute(
            delete(AttachmentLink).where(
                AttachmentLink.household_id == self._household_id,
                AttachmentLink.attachment_id == attachment_id,
                AttachmentLink.entity_type == entity_type,
                AttachmentLink.entity_id == entity_id,
            )
        )
