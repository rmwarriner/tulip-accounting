"""MFA recovery codes — one-time fall-back logins for users who lose their authenticator."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class MfaRecoveryCode(Base):
    """A single-use MFA recovery code, stored as an argon2id hash.

    8 codes are generated per user when they verify TOTP enrollment;
    each ``used_at`` is set the first time it's accepted at
    ``/v1/auth/login/recover``. Used codes are kept for audit
    reconstruction (don't delete on consumption).
    """

    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "user_id"],
            ["users.household_id", "users.id"],
            name="fk_mfa_recovery_codes_user",
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
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
