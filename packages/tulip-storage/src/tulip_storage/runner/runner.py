"""The Runner — see ADR-0002.

Single async-loop poller. Reads ``scheduled_jobs`` for due rows, dispatches
each to its registered handler, records the result in ``scheduled_job_runs``,
and re-schedules recurring jobs. ~150 LOC of core logic; deliberately
small enough to maintain ourselves rather than depend on apscheduler.

ADR-0002 §8 documents the single-uvicorn-worker assumption. Running with
``--workers > 1`` will race two pollers and double-fire jobs. v1
deployment story is single-worker; multi-worker safety is Phase 9.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tulip_storage.models import ScheduledJob, ScheduledJobRun, ScheduledJobRunStatus
from tulip_storage.runner.clock import Clock, default_clock
from tulip_storage.runner.rrule import compute_next_fire

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


log = logging.getLogger("tulip_storage.runner")

#: Backoff schedule per ADR-0002 §7. After the third failure (index 2 in
#: this list), the next attempt promotes the run to ``dead_letter`` and
#: deactivates the parent job.
RETRY_BACKOFF: tuple[timedelta, ...] = (
    timedelta(seconds=60),
    timedelta(minutes=5),
    timedelta(minutes=30),
)
MAX_RETRIES: int = len(RETRY_BACKOFF)

#: Default poll cadence. Tests use a much smaller value or drive the loop
#: manually via ``run_once``.
DEFAULT_POLL_INTERVAL_SECONDS: float = 30.0


HandlerCallback = Callable[[ScheduledJob, Clock], Awaitable[None]]


class IdempotencyKeyConflictError(ValueError):
    """Raised when ``schedule_one`` / ``schedule_recurring`` collides on idempotency_key.

    The unique partial index on ``(household_id, kind, idempotency_key)``
    rejects the insert; we surface a typed exception rather than letting
    ``IntegrityError`` leak through. Caller decides whether to ignore
    (the existing job is good enough) or surface to the user.
    """


class Runner:
    """In-process scheduler. See ADR-0002 §1 for the architecture decision."""

    def __init__(
        self,
        session_maker: sessionmaker[Session],
        *,
        clock: Clock = default_clock,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        """Construct (without starting) the runner.

        Args:
            session_maker: SQLAlchemy session factory the runner uses for
                its DB writes. Each poll tick opens a fresh session.
            clock: Time injection per ADR-0002 §6. Tests pass a callable
                whose return value they advance manually.
            poll_interval_seconds: How often the loop polls when no jobs
                are due. The loop fires immediately if jobs are already
                due, regardless.

        """
        self._session_maker = session_maker
        self._clock = clock
        self._poll_interval = poll_interval_seconds
        self._handlers: dict[str, HandlerCallback] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    # ---- Public API per ADR-0002 §2 ----------------------------------

    def register_handler(self, kind: str, callback: HandlerCallback) -> None:
        """Register a handler for a job ``kind``.

        Sync because handler registration happens at import time, not
        inside the event loop. Re-registering replaces the prior handler
        — useful for tests and live-reload.
        """
        self._handlers[kind] = callback

    def schedule_one(
        self,
        *,
        household_id: UUID,
        kind: str,
        payload: dict[str, Any],
        fire_at: datetime,
        idempotency_key: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> UUID:
        """Insert a one-shot job. Returns the new ``scheduled_jobs.id``."""
        job_id = uuid4()
        with self._session_maker() as session:
            session.add(
                ScheduledJob(
                    household_id=household_id,
                    id=job_id,
                    kind=kind,
                    payload=payload,
                    rrule=None,
                    dtstart=fire_at,  # unused for one-shot; kept stable
                    next_run_at=fire_at,
                    idempotency_key=idempotency_key,
                    is_active=True,
                    created_by_user_id=created_by_user_id,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                msg = (
                    f"idempotency_key {idempotency_key!r} already in use for "
                    f"kind={kind} in household {household_id}"
                )
                raise IdempotencyKeyConflictError(msg) from exc
        return job_id

    def schedule_recurring(
        self,
        *,
        household_id: UUID,
        kind: str,
        payload: dict[str, Any],
        rrule: str,
        start_at: datetime | None = None,
        idempotency_key: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> UUID:
        """Insert a recurring job. Returns the new ``scheduled_jobs.id``."""
        baseline = start_at or self._clock()
        first_fire = compute_next_fire(rrule, dtstart=baseline, after=baseline, inclusive=True)
        if first_fire is None:
            msg = f"RRULE {rrule!r} has no occurrence at or after {baseline.isoformat()}"
            raise ValueError(msg)
        job_id = uuid4()
        with self._session_maker() as session:
            session.add(
                ScheduledJob(
                    household_id=household_id,
                    id=job_id,
                    kind=kind,
                    payload=payload,
                    rrule=rrule,
                    dtstart=baseline,
                    next_run_at=first_fire,
                    idempotency_key=idempotency_key,
                    is_active=True,
                    created_by_user_id=created_by_user_id,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                msg = (
                    f"idempotency_key {idempotency_key!r} already in use for "
                    f"kind={kind} in household {household_id}"
                )
                raise IdempotencyKeyConflictError(msg) from exc
        return job_id

    def cancel(self, household_id: UUID, job_id: UUID) -> None:
        """Flip ``is_active=False``. Idempotent. Run rows untouched."""
        with self._session_maker() as session:
            job = session.execute(
                select(ScheduledJob).where(
                    ScheduledJob.household_id == household_id,
                    ScheduledJob.id == job_id,
                )
            ).scalar_one_or_none()
            if job is None:
                return
            job.is_active = False
            session.commit()

    # ---- Loop lifecycle -----------------------------------------------

    async def start(self) -> None:
        """Spawn the polling loop as an asyncio task.

        Idempotent — calling twice does nothing extra. Cancel via :meth:`stop`.
        """
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="tulip_runner")

    async def stop(self) -> None:
        """Signal the loop to exit and wait for it to drain."""
        if self._stop_event is None or self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: S110
                # Cancellation is expected; any other exception during
                # shutdown is logged via the loop's own exception handling.
                pass
        self._task = None
        self._stop_event = None

    async def _loop(self) -> None:
        assert self._stop_event is not None  # set by start()  # noqa: S101
        while not self._stop_event.is_set():
            try:
                fired = await self.run_once()
            except Exception:
                log.exception("runner.poll_failed")
                fired = 0
            # If we fired anything, immediately poll again — the next batch
            # may already be due. Otherwise wait the poll interval.
            if fired > 0:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval,
                )
            except TimeoutError:
                continue

    # ---- Polling --------------------------------------------------

    async def run_once(self) -> int:
        """Run all jobs whose ``next_run_at`` is at or before ``clock()`` once.

        Returns the number of jobs dispatched (regardless of success /
        failure). Tests call this directly to drive the loop without
        starting / stopping.
        """
        now = self._clock()
        due = self._fetch_due(now)
        for job_snapshot in due:
            await self._dispatch(job_snapshot, now)
        return len(due)

    def _fetch_due(self, now: datetime) -> list[ScheduledJob]:
        """Return active jobs whose ``next_run_at <= now``.

        Loaded into a fresh session and detached so each ``_dispatch`` can
        open its own session. We avoid passing the loop's session into
        handler code — handlers' DB writes need their own transaction
        scope.
        """
        with self._session_maker() as session:
            rows = (
                session.execute(
                    select(ScheduledJob).where(
                        ScheduledJob.is_active.is_(True),
                        ScheduledJob.next_run_at <= now,
                    )
                )
                .scalars()
                .all()
            )
            # Detach so we can use these objects after the session closes.
            for row in rows:
                session.expunge(row)
        return list(rows)

    async def _dispatch(self, job: ScheduledJob, now: datetime) -> None:
        """Run one job. Records start, calls handler, records outcome."""
        handler = self._handlers.get(job.kind)
        if handler is None:
            log.warning(
                "runner.no_handler",
                extra={"job_id": str(job.id), "kind": job.kind},
            )
            self._record_outcome(
                job,
                started_at=now,
                completed_at=now,
                status=ScheduledJobRunStatus.FAILED,
                last_error=f"no handler registered for kind={job.kind!r}",
                advance_next_run_at=False,
            )
            return

        run_id = uuid4()
        start = self._clock()
        self._record_start(job, run_id=run_id, started_at=start)

        try:
            await handler(job, self._clock)
        except Exception as exc:
            log.exception(
                "runner.handler_failed",
                extra={"job_id": str(job.id), "kind": job.kind},
            )
            self._on_failure(job, run_id=run_id, started_at=start, error=str(exc))
            return

        completed = self._clock()
        self._record_completion(
            job,
            run_id=run_id,
            started_at=start,
            completed_at=completed,
            status=ScheduledJobRunStatus.SUCCESS,
        )
        self._advance_after_success(job, completed_at=completed)

    # ---- Per-fire bookkeeping ----------------------------------------

    def _record_start(self, job: ScheduledJob, *, run_id: UUID, started_at: datetime) -> None:
        with self._session_maker() as session:
            session.add(
                ScheduledJobRun(
                    household_id=job.household_id,
                    id=run_id,
                    scheduled_job_id=job.id,
                    started_at=started_at,
                    status=ScheduledJobRunStatus.RUNNING,
                    retry_count=0,
                )
            )
            session.commit()

    def _record_completion(
        self,
        job: ScheduledJob,
        *,
        run_id: UUID,
        started_at: datetime,
        completed_at: datetime,
        status: ScheduledJobRunStatus,
    ) -> None:
        with self._session_maker() as session:
            run = session.execute(
                select(ScheduledJobRun).where(
                    ScheduledJobRun.household_id == job.household_id,
                    ScheduledJobRun.id == run_id,
                )
            ).scalar_one()
            run.completed_at = completed_at
            run.status = status
            session.commit()

    def _record_outcome(
        self,
        job: ScheduledJob,
        *,
        started_at: datetime,
        completed_at: datetime,
        status: ScheduledJobRunStatus,
        last_error: str | None = None,
        advance_next_run_at: bool,
    ) -> None:
        """One-shot insert of a completed run row (used for no-handler case)."""
        with self._session_maker() as session:
            session.add(
                ScheduledJobRun(
                    household_id=job.household_id,
                    id=uuid4(),
                    scheduled_job_id=job.id,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=status,
                    retry_count=0,
                    last_error=last_error,
                )
            )
            if advance_next_run_at:
                attached = session.execute(
                    select(ScheduledJob).where(
                        ScheduledJob.household_id == job.household_id,
                        ScheduledJob.id == job.id,
                    )
                ).scalar_one()
                attached.last_run_at = completed_at
            session.commit()

    def _on_failure(
        self,
        job: ScheduledJob,
        *,
        run_id: UUID,
        started_at: datetime,
        error: str,
    ) -> None:
        """Update the run row with ``failed`` + retry_count, schedule next attempt."""
        with self._session_maker() as session:
            run = session.execute(
                select(ScheduledJobRun).where(
                    ScheduledJobRun.household_id == job.household_id,
                    ScheduledJobRun.id == run_id,
                )
            ).scalar_one()
            attached_job = session.execute(
                select(ScheduledJob).where(
                    ScheduledJob.household_id == job.household_id,
                    ScheduledJob.id == job.id,
                )
            ).scalar_one()

            # Count prior failures for this job since the last success.
            prior_failures = (
                session.execute(
                    select(ScheduledJobRun).where(
                        ScheduledJobRun.household_id == job.household_id,
                        ScheduledJobRun.scheduled_job_id == job.id,
                        ScheduledJobRun.status == ScheduledJobRunStatus.FAILED,
                    )
                )
                .scalars()
                .all()
            )
            retry_count = len(prior_failures)

            now = self._clock()
            run.completed_at = now
            run.last_error = error
            run.retry_count = retry_count

            if retry_count >= MAX_RETRIES:
                # Dead-letter: deactivate the job + flip the run status.
                run.status = ScheduledJobRunStatus.DEAD_LETTER
                attached_job.is_active = False
                log.error(
                    "runner.dead_letter",
                    extra={"job_id": str(job.id), "kind": job.kind},
                )
            else:
                run.status = ScheduledJobRunStatus.FAILED
                # Schedule a retry; backoff index is the number of prior
                # failures (0-indexed into RETRY_BACKOFF).
                attached_job.next_run_at = now + RETRY_BACKOFF[retry_count]
            session.commit()

    def _advance_after_success(self, job: ScheduledJob, *, completed_at: datetime) -> None:
        """Re-schedule a recurring job; deactivate a one-shot."""
        with self._session_maker() as session:
            attached = session.execute(
                select(ScheduledJob).where(
                    ScheduledJob.household_id == job.household_id,
                    ScheduledJob.id == job.id,
                )
            ).scalar_one()
            attached.last_run_at = completed_at
            if attached.rrule is None:
                # One-shot: done.
                attached.is_active = False
            else:
                # SQLite drops tzinfo from DateTime(timezone=True) columns
                # on read; normalize so dateutil can compare.
                stored_dtstart = attached.dtstart
                if stored_dtstart.tzinfo is None:
                    stored_dtstart = stored_dtstart.replace(tzinfo=completed_at.tzinfo)
                next_fire = compute_next_fire(
                    attached.rrule,
                    dtstart=stored_dtstart,
                    after=completed_at,
                )
                if next_fire is None:
                    # RRULE exhausted (COUNT/UNTIL boundary).
                    attached.is_active = False
                else:
                    attached.next_run_at = next_fire
            session.commit()
