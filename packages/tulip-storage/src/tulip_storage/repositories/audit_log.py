"""AuditLogWriter — the single chokepoint for writing audit_log rows.

ARCHITECTURE.md §4.1 / §7.2 mandates that every business mutation produces
an audit_log row. The API layer threads an AuditLogWriter into every
mutation path; this class never reads the table back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from tulip_storage.models import AuditLog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AuditLogWriter:
    """Append-only writer for the audit_log table within one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the writer to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def write(
        self,
        *,
        action: str,
        actor_kind: str,
        entity_type: str,
        entity_id: UUID,
        actor_user_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Write a single audit_log row and return it."""
        row = AuditLog(
            id=uuid4(),
            household_id=self._household_id,
            occurred_at=datetime.now(tz=UTC),
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_snapshot=before,
            after_snapshot=after,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_=metadata,
        )
        self._session.add(row)
        self._session.flush()
        return row
