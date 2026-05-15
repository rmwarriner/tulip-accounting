"""``pending_household_erasures`` — two-step household-delete confirmation.

Right-to-erasure (GDPR Art. 17 / CCPA §1798.105) is destructive enough
that we gate the actual ``DELETE /v1/households/me`` call behind a fresh
confirmation token issued by ``POST /v1/households/me/erase-request``.
This table stores that token. A request is valid for a short TTL (15
minutes) — long enough to read the confirmation copy, short enough that
a token leaked from an old browser tab can't be replayed.

One row per household at most (PK ``household_id``); requesting again
overwrites the previous token. See H-2 in #235.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class PendingHouseholdErasure(Base):
    """One household's outstanding erasure confirmation."""

    __tablename__ = "pending_household_erasures"

    household_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("households.id", ondelete="CASCADE"),
        primary_key=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
