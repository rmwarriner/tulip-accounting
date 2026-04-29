"""PeriodRepository — household-scoped CRUD over the periods table."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_type
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import Period, PeriodStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PeriodRepository:
    """CRUD + status transitions for accounting periods."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        start_date: date_type,
        end_date: date_type,
        status: PeriodStatus = PeriodStatus.OPEN,
    ) -> Period:
        """Create a new Period."""
        p = Period(
            household_id=self._household_id,
            id=uuid4(),
            start_date=start_date,
            end_date=end_date,
            status=status,
        )
        self._session.add(p)
        self._session.flush()
        return p

    def get(self, period_id: UUID) -> Period | None:
        """Return the Period with the given id within this household, or None."""
        return self._session.execute(
            select(Period).where(
                Period.household_id == self._household_id,
                Period.id == period_id,
            )
        ).scalar_one_or_none()

    def find_for_date(self, on: date_type) -> Period | None:
        """Return the (single) Period that contains `on`, or None."""
        return self._session.execute(
            select(Period).where(
                Period.household_id == self._household_id,
                Period.start_date <= on,
                Period.end_date >= on,
            )
        ).scalar_one_or_none()

    def list_all(self) -> list[Period]:
        """Return every Period for this household, newest first."""
        return list(
            self._session.execute(
                select(Period)
                .where(Period.household_id == self._household_id)
                .order_by(Period.start_date.desc())
            )
            .scalars()
            .all()
        )

    def close(self, period_id: UUID, *, by_user_id: UUID | None) -> Period:
        """Mark a period soft-closed."""
        p = self.get(period_id)
        if p is None:
            raise LookupError(f"period {period_id} not found")
        p.status = PeriodStatus.SOFT_CLOSED
        p.closed_by_user_id = by_user_id
        p.closed_at = datetime.now(tz=UTC)
        self._session.flush()
        return p

    def reopen(self, period_id: UUID) -> Period:
        """Re-open a previously soft-closed period."""
        p = self.get(period_id)
        if p is None:
            raise LookupError(f"period {period_id} not found")
        p.status = PeriodStatus.OPEN
        p.closed_at = None
        p.closed_by_user_id = None
        self._session.flush()
        return p
