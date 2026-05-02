"""ScheduledJobRepository — household-scoped reads over scheduled_jobs.

The Runner (see ADR-0002) is the single writer for ``scheduled_jobs``.
This repo provides **read-only** queries for the API (P4.3.c) — listing
the household's schedules, looking up by idempotency key, and inspecting
recent runs. Writes still go through ``Runner.schedule_one`` /
``schedule_recurring`` / ``cancel``; the architecture test enforces that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from tulip_storage.models import ScheduledJob, ScheduledJobRun

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ScheduledJobRepository:
    """Household-scoped read-only access to scheduled_jobs + scheduled_job_runs."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, job_id: UUID) -> ScheduledJob | None:
        """Return the ScheduledJob with the given id, or None."""
        return self._session.execute(
            select(ScheduledJob).where(
                ScheduledJob.household_id == self._household_id,
                ScheduledJob.id == job_id,
            )
        ).scalar_one_or_none()

    def get_by_idempotency_key(self, *, kind: str, idempotency_key: str) -> ScheduledJob | None:
        """Find the (at most one) job for ``(household, kind, key)``.

        The unique partial index per ADR-0002 §5 guarantees zero or one
        match. Useful for "does this envelope already have a refill
        schedule?" queries.
        """
        return self._session.execute(
            select(ScheduledJob).where(
                ScheduledJob.household_id == self._household_id,
                ScheduledJob.kind == kind,
                ScheduledJob.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()

    def list_active(self, *, kind: str | None = None) -> list[ScheduledJob]:
        """Return all active jobs in this household, optionally filtered by kind.

        Ordered by ``next_run_at`` ascending (the polling order) so the
        first row is the next thing the runner will fire.
        """
        query = select(ScheduledJob).where(
            ScheduledJob.household_id == self._household_id,
            ScheduledJob.is_active.is_(True),
        )
        if kind is not None:
            query = query.where(ScheduledJob.kind == kind)
        query = query.order_by(ScheduledJob.next_run_at.asc())
        return list(self._session.execute(query).scalars().all())

    def list_runs(self, job_id: UUID, *, limit: int = 20) -> list[ScheduledJobRun]:
        """Return the most recent ``limit`` runs for a job, newest first."""
        return list(
            self._session.execute(
                select(ScheduledJobRun)
                .where(
                    ScheduledJobRun.household_id == self._household_id,
                    ScheduledJobRun.scheduled_job_id == job_id,
                )
                .order_by(ScheduledJobRun.started_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
