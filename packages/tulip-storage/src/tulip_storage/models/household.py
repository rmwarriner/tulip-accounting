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
        # ``values_callable`` stores the enum *value* (e.g. ``"optional"``),
        # not the *name* (``"OPTIONAL"``). Matches the lowercase form in
        # ARCHITECTURE.md §4.1 and the migration's ``server_default``, and
        # makes raw SQL (``UPDATE … SET mfa_policy='required_for_admins'``)
        # round-trip correctly. Without this, only inserts via SQLAlchemy
        # work — rows written by ``server_default`` or raw SQL would fail
        # the enum-lookup on SELECT.
        SAEnum(
            MfaPolicy,
            native_enum=False,
            length=30,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
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
