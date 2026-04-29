"""Session model — refresh-token-backed authentication sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class Session(Base):
    """An authentication session.

    Created on successful login; rotated on refresh; revoked on logout or
    explicit admin action. The refresh token is never persisted in
    plaintext — only the SHA-256 hash is stored.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "user_id"],
            ["users.household_id", "users.id"],
            name="fk_sessions_user",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    household_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    household: Mapped[Household] = relationship()
