"""``daily_insights`` handler — runs anomaly detection over per-envelope spend (P6.3).

Schedule one job per household (typically nightly via ``schedule_recurring``)
with kind ``daily_insights``. The handler:

1. Lists active envelopes for the household.
2. For each envelope, builds a 60-day daily-amount series from POSTED
   shadow postings on that envelope's pool.
3. Runs :func:`tulip_core.insights.find_anomalies` against the series.
4. Writes one ``notifications`` row per detected anomaly.

The AI forecast capability (which would ride alongside the anomaly
detector here) is deferred to a follow-up slice — the handler's
structure leaves an obvious place to add it: after step 3, take the
same series + envelope context and call into ``tulip_ai`` for a
forecast. The notifications row distinguishes via ``kind`` (``anomaly``
vs ``forecast``) so the two surfaces coexist cleanly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from tulip_core.insights import find_anomalies
from tulip_storage.models import (
    AllocationPool,
    Envelope,
    NotificationKind,
    NotificationSeverity,
    PoolType,
)
from tulip_storage.repositories import NotificationRepository, ShadowTransactionRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.clock import Clock
    from tulip_storage.runner.runner import HandlerCallback


_SERIES_DAYS = 60
_PRODUCED_BY = "daily_insights"


def make_daily_insights_handler(
    session_maker: sessionmaker[Session],
) -> HandlerCallback:
    """Build the ``daily_insights`` handler bound to a session factory."""

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        with session_maker() as session:
            _process(session, job=job, clock=clock)
            session.commit()

    return handle


def _process(session: Session, *, job: ScheduledJob, clock: Clock) -> None:
    """Inner: iterate envelopes, compute series, write notification rows."""
    today = clock().date()
    notifications = NotificationRepository(session, job.household_id)

    envelopes = session.execute(
        select(AllocationPool, Envelope)
        .join(
            Envelope,
            (Envelope.household_id == AllocationPool.household_id)
            & (Envelope.pool_id == AllocationPool.id),
        )
        .where(
            AllocationPool.household_id == job.household_id,
            AllocationPool.pool_type == PoolType.ENVELOPE,
            AllocationPool.is_active.is_(True),
        )
    ).all()

    for pool, envelope in envelopes:
        series = _daily_spend_series(
            session,
            household_id=job.household_id,
            pool_id=pool.id,
            currency=pool.currency,
            today=today,
        )
        for anomaly in find_anomalies(series):
            notifications.create(
                kind=NotificationKind.ANOMALY.value,
                severity=_to_notification_severity(anomaly.severity),
                title=f"Unusual spend on {pool.name}",
                body=(
                    f"Spending of {anomaly.amount} {pool.currency} on "
                    f"{anomaly.sample_date.isoformat()} is "
                    f"{anomaly.z_score:.1f} sigma above the 30-day rolling "
                    f"mean of {anomaly.rolling_mean:.2f}."
                ),
                produced_by=_PRODUCED_BY,
                entity_type="envelope",
                entity_id=pool.id,
            )
        del envelope  # unused for now; AI forecast slice will read it.


def _daily_spend_series(
    session: Session,
    *,
    household_id: object,
    pool_id: object,
    currency: str,
    today: date,
) -> list[tuple[date, Decimal]]:
    """Build a zero-filled daily-spend series via the repo chokepoint.

    Returns ``_SERIES_DAYS`` rows ending on ``today`` (inclusive), with
    zero-filled gaps so the rolling-window math has a uniform grid.
    """
    cutoff = today - timedelta(days=_SERIES_DAYS - 1)
    shadow_repo = ShadowTransactionRepository(session, household_id)  # type: ignore[arg-type]
    by_date = shadow_repo.daily_spend_series_for_pool(
        pool_id,  # type: ignore[arg-type]
        currency=currency,
        from_date=cutoff,
        to_date=today,
    )
    return [
        (cutoff + timedelta(days=i), by_date.get(cutoff + timedelta(days=i), Decimal("0")))
        for i in range(_SERIES_DAYS)
    ]


def _to_notification_severity(severity: object) -> str:
    """Map ``tulip_core.insights.AnomalySeverity`` to the notifications enum value."""
    # AnomalySeverity is a string-valued Enum; the values line up 1:1.
    sev = getattr(severity, "value", str(severity))
    if sev in (
        NotificationSeverity.INFO.value,
        NotificationSeverity.WARNING.value,
        NotificationSeverity.CRITICAL.value,
    ):
        return sev
    return NotificationSeverity.INFO.value


# A no-op import to silence the unused-name warning when the handler's
# wrapper-only code path doesn't touch the clock.
_ = datetime, UTC


__all__ = ["make_daily_insights_handler"]
