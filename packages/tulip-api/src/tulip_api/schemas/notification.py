"""Schemas for ``/v1/notifications`` (P6.3)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationRead(BaseModel):
    """One row of the notifications inbox."""

    id: UUID
    created_at: datetime
    kind: str
    severity: str
    title: str
    body: str
    produced_by: str
    entity_type: str | None
    entity_id: UUID | None
    dismissed_at: datetime | None
    ai_invocation_id: UUID | None = Field(default=None)
