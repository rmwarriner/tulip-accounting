"""Period model — open / soft-closed accounting window."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class PeriodStatus(Enum):
    """Open or soft-closed."""

    OPEN = "open"
    SOFT_CLOSED = "soft_closed"


class Period(Base):
    """An accounting period.

    Soft-close is the v1 model — closed periods still accept postings (the
    API logs an audit warning). True immutability is deferred to Postgres.
    """

    __tablename__ = "periods"

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    start_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        SAEnum(PeriodStatus, native_enum=False, length=20), nullable=False
    )
    closed_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    household: Mapped[Household] = relationship()
