"""``notifications`` — daily-insights inbox (P6.3, ARCHITECTURE.md §6.2)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class NotificationKind(Enum):
    """What the row is announcing."""

    ANOMALY = "anomaly"
    FORECAST = "forecast"
    RUNOUT = "runout"
    ON_TRACK = "on_track"


class NotificationSeverity(Enum):
    """How loudly the row wants the user's attention."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Notification(Base):
    """One inbox row.

    Written exclusively by the ``daily_insights`` scheduler handler in
    P6.3; future capabilities (period close, backup failures) reuse the
    table by passing their own ``produced_by`` tag.
    """

    __tablename__ = "notifications"

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    produced_by: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_invocation_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
