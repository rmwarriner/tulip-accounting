"""NotificationRepository — daily-insights inbox (P6.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import Notification

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class NotificationRepository:
    """CRUD + dismiss for the household's notification inbox."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        kind: str,
        severity: str,
        title: str,
        body: str,
        produced_by: str,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        ai_invocation_id: UUID | None = None,
    ) -> Notification:
        """Insert one notification row."""
        row = Notification(
            household_id=self._household_id,
            id=uuid4(),
            kind=kind,
            severity=severity,
            title=title,
            body=body,
            produced_by=produced_by,
            entity_type=entity_type,
            entity_id=entity_id,
            ai_invocation_id=ai_invocation_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_active(self) -> list[Notification]:
        """Undismissed notifications, newest first."""
        return list(
            self._session.execute(
                select(Notification)
                .where(
                    Notification.household_id == self._household_id,
                    Notification.dismissed_at.is_(None),
                )
                .order_by(Notification.created_at.desc())
            )
            .scalars()
            .all()
        )

    def list_all(self) -> list[Notification]:
        """All notifications including dismissed, newest first."""
        return list(
            self._session.execute(
                select(Notification)
                .where(Notification.household_id == self._household_id)
                .order_by(Notification.created_at.desc())
            )
            .scalars()
            .all()
        )

    def get(self, notification_id: UUID) -> Notification | None:
        """Return one notification, or ``None`` if not in this household."""
        return self._session.execute(
            select(Notification).where(
                Notification.household_id == self._household_id,
                Notification.id == notification_id,
            )
        ).scalar_one_or_none()

    def dismiss(self, notification_id: UUID) -> Notification | None:
        """Stamp ``dismissed_at`` on the row. Idempotent on already-dismissed."""
        row = self.get(notification_id)
        if row is None:
            return None
        if row.dismissed_at is None:
            row.dismissed_at = datetime.now(UTC)
            self._session.flush()
        return row
