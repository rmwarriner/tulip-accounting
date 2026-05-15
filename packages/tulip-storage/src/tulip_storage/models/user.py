"""User model — belongs to exactly one household in v1."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class UserRole(Enum):
    """Per-household role; see ARCHITECTURE §3.4."""

    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class User(Base):
    """A user within a household."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("household_id", "email", name="uq_users_household_email"),)

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=20), nullable=False
    )
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(nullable=True)
    # Encrypted ``{provider: api_key}``; overrides the household's keys
    # for this user. See ADR-0005 §Q2.
    ai_keys_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Per-user AI policy override (#239). NULL = inherit household. The
    # resolver merges this with ``households.ai_policy`` using max-severity
    # wins per ADR-0005 §Q5 — a user can ratchet *up* (stricter) but not
    # ratchet down below the household's floor.
    ai_policy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    household: Mapped[Household] = relationship()
