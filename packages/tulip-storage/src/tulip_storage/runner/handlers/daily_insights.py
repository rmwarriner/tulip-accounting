"""``daily_insights`` handler — runs anomaly detection + AI forecasts (P6.3 / P6.5.c).

Schedule one job per household (typically nightly via ``schedule_recurring``)
with kind ``daily_insights``. The handler:

1. Lists active envelopes for the household.
2. For each envelope, builds a 60-day daily-spend series from POSTED
   shadow postings on that envelope's pool.
3. Runs :func:`tulip_core.insights.find_anomalies` against the series.
4. Writes one ``notifications`` row per detected anomaly.
5. If a forecaster is wired in, calls it with an envelope-shaped
   :class:`ForecastRequest`; on text return, writes a ``kind=forecast``
   row with ``entity_type=envelope``.
6. (P6.5.c) Lists active sinking funds for the household. For each,
   builds a 60-day daily-contribution series, fetches the current
   balance, and calls the forecaster with a sinking-fund-shaped
   :class:`ForecastRequest` (target_amount / target_date / current_balance
   populated). On text return, writes a ``kind=forecast`` row with
   ``entity_type=sinking_fund``.

The notification rows distinguish envelopes from sinking funds via
``entity_type`` so a single ``kind=forecast`` channel serves both.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import select

from tulip_core.insights import find_anomalies
from tulip_storage.models import (
    AllocationPool,
    Envelope,
    NotificationKind,
    NotificationSeverity,
    PoolType,
    SinkingFund,
)
from tulip_storage.repositories import NotificationRepository, ShadowTransactionRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.clock import Clock
    from tulip_storage.runner.runner import HandlerCallback


_SERIES_DAYS = 60
_PRODUCED_BY = "daily_insights"


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    """Everything the forecaster needs for one envelope or sinking fund (P6.5.c).

    For envelopes, ``target_amount`` / ``target_date`` / ``current_balance``
    are ``None`` and ``series`` is the daily spend series. For sinking
    funds, all three are populated and ``series`` is the daily
    contribution series. The forecaster owns prompt construction; the
    handler just hands it the context.
    """

    household_id: UUID
    pool_id: UUID
    pool_name: str
    pool_currency: str
    pool_kind: Literal["envelope", "sinking_fund"]
    series: list[tuple[date, Decimal]]
    target_amount: Decimal | None
    target_date: date | None
    current_balance: Decimal | None


#: Callback the handler invokes (when set) to obtain a forecast for one
#: pool. Returning ``None`` (or an empty string) skips the forecast
#: notification — the handler-side "AI not configured" signal. The
#: callback owns its own provider call, audit row, and policy
#: resolution; the handler just writes the notification row.
ForecasterCallback = Callable[[ForecastRequest], Awaitable[str | None]]


def make_daily_insights_handler(
    session_maker: sessionmaker[Session],
    *,
    forecaster: ForecasterCallback | None = None,
) -> HandlerCallback:
    """Build the ``daily_insights`` handler bound to a session factory.

    When ``forecaster`` is non-None, the handler calls it per envelope
    after the anomaly loop and writes a ``kind=forecast`` notification
    for any envelope the forecaster returned text for. When ``None``,
    only anomaly notifications fire — matches the P6.3 default before
    P6.3.b wired up the AI forecaster.
    """

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        with session_maker() as session:
            await _process(session, job=job, clock=clock, forecaster=forecaster)
            session.commit()

    return handle


async def _process(
    session: Session,
    *,
    job: ScheduledJob,
    clock: Clock,
    forecaster: ForecasterCallback | None,
) -> None:
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
        if forecaster is not None:
            request = ForecastRequest(
                household_id=job.household_id,
                pool_id=pool.id,
                pool_name=pool.name,
                pool_currency=pool.currency,
                pool_kind="envelope",
                series=series,
                target_amount=None,
                target_date=None,
                current_balance=None,
            )
            forecast_text = await forecaster(request)
            if forecast_text:
                notifications.create(
                    kind=NotificationKind.FORECAST.value,
                    severity=NotificationSeverity.INFO.value,
                    title=f"Forecast for {pool.name}",
                    body=forecast_text,
                    produced_by=_PRODUCED_BY,
                    entity_type="envelope",
                    entity_id=pool.id,
                )
        del envelope  # envelope-specific fields are inherited from the pool today.

    if forecaster is not None:
        await _forecast_sinking_funds(
            session,
            job=job,
            today=today,
            notifications=notifications,
            forecaster=forecaster,
        )


async def _forecast_sinking_funds(
    session: Session,
    *,
    job: ScheduledJob,
    today: date,
    notifications: NotificationRepository,
    forecaster: ForecasterCallback,
) -> None:
    """Iterate active sinking funds; call the forecaster with target context.

    Each sinking fund gets a 60-day daily-contribution series + its
    current balance + its locked ``target_amount`` / ``target_date``.
    On a non-empty forecast text, writes one ``kind=forecast`` /
    ``entity_type=sinking_fund`` notification.
    """
    sinking_funds = session.execute(
        select(AllocationPool, SinkingFund)
        .join(
            SinkingFund,
            (SinkingFund.household_id == AllocationPool.household_id)
            & (SinkingFund.pool_id == AllocationPool.id),
        )
        .where(
            AllocationPool.household_id == job.household_id,
            AllocationPool.pool_type == PoolType.SINKING_FUND,
            AllocationPool.is_active.is_(True),
        )
    ).all()

    shadow_repo = ShadowTransactionRepository(session, job.household_id)
    cutoff = today - timedelta(days=_SERIES_DAYS - 1)

    for pool, sinking_fund in sinking_funds:
        contribution_map = shadow_repo.daily_contribution_series_for_pool(
            pool.id,
            currency=pool.currency,
            from_date=cutoff,
            to_date=today,
        )
        series = [
            (
                cutoff + timedelta(days=i),
                contribution_map.get(cutoff + timedelta(days=i), Decimal("0")),
            )
            for i in range(_SERIES_DAYS)
        ]
        balances = shadow_repo.balance_for_pool(pool.id, currency=pool.currency)
        current_balance = balances.get(pool.currency, Decimal("0"))
        request = ForecastRequest(
            household_id=job.household_id,
            pool_id=pool.id,
            pool_name=pool.name,
            pool_currency=pool.currency,
            pool_kind="sinking_fund",
            series=series,
            target_amount=sinking_fund.target_amount,
            target_date=sinking_fund.target_date,
            current_balance=current_balance,
        )
        forecast_text = await forecaster(request)
        if forecast_text:
            notifications.create(
                kind=NotificationKind.FORECAST.value,
                severity=NotificationSeverity.INFO.value,
                title=f"Forecast for {pool.name}",
                body=forecast_text,
                produced_by=_PRODUCED_BY,
                entity_type="sinking_fund",
                entity_id=pool.id,
            )


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


__all__ = ["ForecastRequest", "ForecasterCallback", "make_daily_insights_handler"]
