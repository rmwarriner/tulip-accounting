"""Household (= tenant) model."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class MfaPolicy(Enum):
    """Per-household MFA policy. See ARCHITECTURE §4.1."""

    OPTIONAL = "optional"
    REQUIRED_FOR_ADMINS = "required_for_admins"
    REQUIRED_FOR_ALL = "required_for_all"


class Household(Base):
    """A household — the unit of tenancy.

    Every domain entity carries `household_id`; cross-tenant queries are
    only possible via an explicit admin scope.
    """

    __tablename__ = "households"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    mfa_policy: Mapped[MfaPolicy] = mapped_column(
        SAEnum(MfaPolicy, native_enum=False, length=30),
        nullable=False,
        default=MfaPolicy.OPTIONAL,
        server_default=MfaPolicy.OPTIONAL.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
