"""Tests for the scheduler runner — see ADR-0002."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from tulip_storage.models import (
    Household,
    ScheduledJob,
    ScheduledJobRun,
    ScheduledJobRunStatus,
)
from tulip_storage.runner import (
    IdempotencyKeyConflictError,
    Runner,
)
from tulip_storage.runner.runner import RETRY_BACKOFF

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


# ---- Fixtures + helpers -------------------------------------------------


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


class FakeClock:
    """A controllable clock for the runner. Tests advance time explicitly."""

    def __init__(self, *, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = when


def _runner(session_maker: sessionmaker[Session], *, clock: FakeClock) -> Runner:
    return Runner(session_maker, clock=clock, poll_interval_seconds=0.01)


def _to_utc(dt: datetime) -> datetime:
    """Normalize a possibly-naive datetime to UTC.

    SQLite ignores the ``timezone=True`` flag on ``DateTime`` columns and
    persists naive timestamps. Tests write tz-aware values; on read they
    come back naive. Round-trip through this helper before subtracting.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _job(session: Session, household_id: UUID, job_id: UUID) -> ScheduledJob:
    return session.execute(
        select(ScheduledJob).where(
            ScheduledJob.household_id == household_id,
            ScheduledJob.id == job_id,
        )
    ).scalar_one()


def _runs(session: Session, household_id: UUID, job_id: UUID) -> list[ScheduledJobRun]:
    return list(
        session.execute(
            select(ScheduledJobRun).where(
                ScheduledJobRun.household_id == household_id,
                ScheduledJobRun.scheduled_job_id == job_id,
            )
        )
        .scalars()
        .all()
    )


# ---- TDD entry tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_one_fires_handler_at_fire_at(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    fired: list[UUID] = []

    async def handler(job: ScheduledJob, _clock):
        fired.append(job.id)

    runner.register_handler("test", handler)

    job_id = runner.schedule_one(
        household_id=household.id,
        kind="test",
        payload={},
        fire_at=t0 + timedelta(seconds=10),
    )

    # Not yet due.
    fired_count = await runner.run_once()
    assert fired_count == 0
    assert fired == []

    clock.advance(timedelta(seconds=11))
    fired_count = await runner.run_once()
    assert fired_count == 1
    assert fired == [job_id]

    # One run row, status=success, advance once.
    runs = _runs(session, household.id, job_id)
    assert len(runs) == 1
    assert runs[0].status is ScheduledJobRunStatus.SUCCESS

    # One-shot job is deactivated after fire.
    job = _job(session, household.id, job_id)
    assert job.is_active is False


@pytest.mark.asyncio
async def test_schedule_recurring_reschedules_after_fire(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    async def handler(job: ScheduledJob, _clock):
        pass

    runner.register_handler("daily_test", handler)
    job_id = runner.schedule_recurring(
        household_id=household.id,
        kind="daily_test",
        payload={},
        rrule="FREQ=DAILY",
        start_at=t0,
    )

    # Job's first fire is at or just after t0.
    fired = await runner.run_once()
    assert fired == 1

    runs = _runs(session, household.id, job_id)
    assert len(runs) == 1
    assert runs[0].status is ScheduledJobRunStatus.SUCCESS

    # Next run advanced by ~1 day. SQLite stores naive datetimes for
    # DateTime(timezone=True) columns; normalize before comparing.
    job = _job(session, household.id, job_id)
    assert job.is_active is True
    next_run = _to_utc(job.next_run_at)
    delta = next_run - t0
    assert timedelta(hours=23) < delta < timedelta(hours=25)


# ---- Idempotency --------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_key_rejects_duplicate(
    session_maker: sessionmaker[Session],
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    runner.schedule_one(
        household_id=household.id,
        kind="test",
        payload={},
        fire_at=t0 + timedelta(minutes=10),
        idempotency_key="envelope-123",
    )
    with pytest.raises(IdempotencyKeyConflictError):
        runner.schedule_one(
            household_id=household.id,
            kind="test",
            payload={},
            fire_at=t0 + timedelta(minutes=20),
            idempotency_key="envelope-123",
        )


@pytest.mark.asyncio
async def test_idempotency_key_distinct_kinds_dont_collide(
    session_maker: sessionmaker[Session],
    household: Household,
) -> None:
    # Different kinds, same key — both succeed (per the unique partial
    # index on (household_id, kind, idempotency_key)).
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    runner.schedule_one(
        household_id=household.id,
        kind="kind_a",
        payload={},
        fire_at=t0,
        idempotency_key="shared",
    )
    runner.schedule_one(
        household_id=household.id,
        kind="kind_b",
        payload={},
        fire_at=t0,
        idempotency_key="shared",
    )


# ---- Cancel -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_prevents_future_fires(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    async def handler(job: ScheduledJob, _clock):
        pass

    runner.register_handler("test", handler)
    job_id = runner.schedule_one(
        household_id=household.id,
        kind="test",
        payload={},
        fire_at=t0,
    )
    runner.cancel(household.id, job_id)

    fired = await runner.run_once()
    assert fired == 0

    runs = _runs(session, household.id, job_id)
    assert runs == []


@pytest.mark.asyncio
async def test_cancel_unknown_job_is_silent(
    session_maker: sessionmaker[Session],
    household: Household,
) -> None:
    runner = _runner(session_maker, clock=FakeClock(now=datetime.now(UTC)))
    runner.cancel(household.id, uuid4())  # no exception


# ---- Retry / dead-letter -----------------------------------------------


@pytest.mark.asyncio
async def test_handler_failure_retries_with_backoff(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    call_count = {"n": 0}

    async def flaky_handler(job: ScheduledJob, _clock):
        call_count["n"] += 1
        raise RuntimeError(f"boom #{call_count['n']}")

    runner.register_handler("flaky", flaky_handler)
    job_id = runner.schedule_one(
        household_id=household.id,
        kind="flaky",
        payload={},
        fire_at=t0,
    )

    # First fire fails -> retry scheduled at +60s.
    await runner.run_once()
    job = _job(session, household.id, job_id)
    assert job.is_active is True
    assert (_to_utc(job.next_run_at) - t0) == RETRY_BACKOFF[0]

    # Advance to the retry, fail again -> retry at +5m.
    clock.set(t0 + RETRY_BACKOFF[0] + timedelta(seconds=1))
    await runner.run_once()
    session.expire_all()
    job = _job(session, household.id, job_id)
    next_t1 = clock()
    assert job.is_active is True
    assert (_to_utc(job.next_run_at) - next_t1) == RETRY_BACKOFF[1]

    # Advance to the next retry, fail again -> retry at +30m.
    clock.set(next_t1 + RETRY_BACKOFF[1] + timedelta(seconds=1))
    await runner.run_once()
    session.expire_all()
    job = _job(session, household.id, job_id)
    next_t2 = clock()
    assert job.is_active is True
    assert (_to_utc(job.next_run_at) - next_t2) == RETRY_BACKOFF[2]

    # Fourth attempt: dead-letter, job deactivated.
    clock.set(next_t2 + RETRY_BACKOFF[2] + timedelta(seconds=1))
    await runner.run_once()
    session.expire_all()
    job = _job(session, household.id, job_id)
    assert job.is_active is False

    runs = _runs(session, household.id, job_id)
    statuses = [r.status for r in runs]
    assert statuses.count(ScheduledJobRunStatus.FAILED) == 3
    assert statuses.count(ScheduledJobRunStatus.DEAD_LETTER) == 1


# ---- Edge cases --------------------------------------------------------


@pytest.mark.asyncio
async def test_no_handler_marks_run_failed(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    job_id = runner.schedule_one(
        household_id=household.id,
        kind="orphan",
        payload={},
        fire_at=t0,
    )
    fired = await runner.run_once()
    assert fired == 1

    runs = _runs(session, household.id, job_id)
    assert len(runs) == 1
    assert runs[0].status is ScheduledJobRunStatus.FAILED
    assert "no handler" in (runs[0].last_error or "").lower()


@pytest.mark.asyncio
async def test_recurring_with_count_exhausts(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    async def handler(job: ScheduledJob, _clock):
        pass

    runner.register_handler("limited", handler)
    job_id = runner.schedule_recurring(
        household_id=household.id,
        kind="limited",
        payload={},
        rrule="FREQ=DAILY;COUNT=2",
        start_at=t0,
    )

    # Fire 1.
    await runner.run_once()
    clock.advance(timedelta(days=1, seconds=1))
    # Fire 2.
    await runner.run_once()
    session.expire_all()
    job = _job(session, household.id, job_id)
    # COUNT=2 means two occurrences total; after both fire, no more
    # occurrences and the job is deactivated.
    assert job.is_active is False


@pytest.mark.asyncio
async def test_start_stop_lifecycle(
    session_maker: sessionmaker[Session],
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _runner(session_maker, clock=clock)

    fired: list[UUID] = []

    async def handler(job: ScheduledJob, _clock):
        fired.append(job.id)

    runner.register_handler("test", handler)
    job_id = runner.schedule_one(
        household_id=household.id,
        kind="test",
        payload={},
        fire_at=t0,
    )

    await runner.start()
    # Give the loop a tick to fire.
    await asyncio.sleep(0.05)
    await runner.stop()

    assert fired == [job_id]
