"""``used_mfa_challenges`` — single-use ledger for MFA-challenge JWT ``jti``.

The MFA-challenge JWT minted by ``POST /v1/auth/login`` carries a fresh
``jti`` (UUIDv4). When the caller redeems the challenge at
``/v1/auth/login/mfa`` or ``/v1/auth/login/recover`` — successfully or
unsuccessfully — the ``jti`` is inserted here. Subsequent attempts to
reuse the same token are rejected even if the JWT signature + TTL
otherwise check out, defeating the replay window left open by the
stateless 5-minute TTL alone. See M-7 in #219.

The table is intentionally tiny (jti + expires_at + used_at). Rows can
be purged in bulk once ``expires_at`` is in the past — replaying an
expired JWT would already be rejected by signature/TTL verification.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class UsedMfaChallenge(Base):
    """One redeemed MFA-challenge ``jti``.

    No household scoping — ``jti`` is a UUIDv4 with negligible collision
    probability across the entire deployment, and the JWT already binds
    ``user_id`` + ``household_id`` cryptographically.
    """

    __tablename__ = "used_mfa_challenges"

    jti: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
