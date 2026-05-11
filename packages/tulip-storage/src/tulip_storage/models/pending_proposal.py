"""``pending_proposals`` — agentic-proposal queue (P6.4, ARCHITECTURE.md §6.2)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class ProposalStatus(Enum):
    """Lifecycle state. New proposals are PENDING."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProposalCreatorKind(Enum):
    """Who created the proposal — drives the audit ``actor_kind`` on approve."""

    USER = "user"
    AI_AGENT = "ai_agent"


class PendingProposal(Base):
    """One pending change waiting on user approval.

    The ``payload`` JSON shape is kind-specific; the approve flow's
    executor for that ``kind`` is the authoritative interpreter. Adding
    a new kind = adding an executor + bumping the kind allowlist on the
    create endpoint.
    """

    __tablename__ = "pending_proposals"

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProposalStatus.PENDING.value
    )
    created_by_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    ai_invocation_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
