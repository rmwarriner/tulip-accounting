"""Append-only audit log model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class AuditLog(Base):
    """An append-only audit row.

    Schema follows ARCHITECTURE §4.1. v1 doesn't physically enforce
    immutability (true OS-level append-only is deferred to the Postgres
    phase per §1.3); the application layer simply never updates or
    deletes rows in this table.
    """

    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    household_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)

    household: Mapped[Household] = relationship()
