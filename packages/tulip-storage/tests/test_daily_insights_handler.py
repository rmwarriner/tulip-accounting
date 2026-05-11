"""Integration tests for the ``daily_insights`` runner handler (P6.3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.models import (
    AllocationPool,
    BudgetPeriod,
    ContributionStrategy,
    Envelope,
    Household,
    Notification,
    PoolType,
    RolloverPolicy,
    ScheduledJob,
    ShadowPosting,
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
    SinkingFund,
)
from tulip_storage.runner.handlers import make_daily_insights_handler
from tulip_storage.runner.handlers.daily_insights import ForecastRequest


def _fixed_clock(when: datetime):  # type: ignore[no-untyped-def]
    return lambda: when


def _setup_household(session_maker: sessionmaker[Session]) -> tuple[Household, AllocationPool]:
    """Seed household + active envelope; return both."""
    hh_id = uuid4()
    pool_id = uuid4()
    with session_maker() as s:
        h = Household(id=hh_id, name="Insights House", base_currency="USD")
        s.add(h)
        s.flush()
        pool = AllocationPool(
            household_id=hh_id,
            id=pool_id,
            name="Groceries",
            pool_type=PoolType.ENVELOPE,
            currency="USD",
            visibility="shared",
            is_active=True,
            is_system=False,
        )
        s.add(pool)
        s.flush()
        s.add(
            Envelope(
                household_id=hh_id,
                pool_id=pool_id,
                budget_period=BudgetPeriod.MONTHLY,
                rollover_policy=RolloverPolicy.RESET,
                budget_amount=None,
                refill_rule_json=None,
            )
        )
        s.commit()
        s.refresh(h)
        s.refresh(pool)
        return h, pool


def _spend(
    session_maker: sessionmaker[Session],
    *,
    household_id,
    pool_id,
    when: date,
    amount: Decimal,
) -> None:
    """Insert one POSTED outflow shadow posting on the envelope's pool."""
    with session_maker() as s:
        tx_id = uuid4()
        s.add(
            ShadowTransaction(
                household_id=household_id,
                id=tx_id,
                date=when,
                description="spend",
                reason=ShadowTxReason.SPEND,
                status=ShadowTxStatus.PENDING,
            )
        )
        s.flush()
        # Balanced pair: outflow on pool, inflow on a placeholder (we
        # cheat by adding a +amount on the same pool so the status
        # transition trigger sees a balanced shadow tx).
        s.add_all(
            [
                ShadowPosting(
                    household_id=household_id,
                    id=uuid4(),
                    shadow_transaction_id=tx_id,
                    pool_id=pool_id,
                    amount=-amount,
                    currency="USD",
                ),
                ShadowPosting(
                    household_id=household_id,
                    id=uuid4(),
                    shadow_transaction_id=tx_id,
                    pool_id=pool_id,
                    amount=amount,
                    currency="USD",
                ),
            ]
        )
        s.flush()
        tx = s.get(ShadowTransaction, (household_id, tx_id))
        assert tx is not None
        tx.status = ShadowTxStatus.POSTED
        s.commit()


def _make_job(household_id) -> ScheduledJob:
    """Build a minimal ScheduledJob in memory; the handler doesn't persist it."""
    return ScheduledJob(
        household_id=household_id,
        id=uuid4(),
        kind="daily_insights",
        payload={},
        rrule=None,
        dtstart=datetime.now(UTC),
        next_run_at=datetime.now(UTC),
        is_active=True,
    )


def test_no_anomalies_no_notifications(
    session_maker: sessionmaker[Session],
) -> None:
    """Flat spending series → no anomalies → no notification rows."""
    household, pool = _setup_household(session_maker)
    today = date(2026, 4, 1)
    for i in range(45):
        _spend(
            session_maker,
            household_id=household.id,
            pool_id=pool.id,
            when=today - timedelta(days=44 - i),
            amount=Decimal("10.00"),
        )
    handler = make_daily_insights_handler(session_maker)
    asyncio.run(
        handler(_make_job(household.id), _fixed_clock(datetime(2026, 4, 1, 12, 0, tzinfo=UTC)))
    )
    with session_maker() as s:
        from sqlalchemy import select

        rows = s.execute(select(Notification)).scalars().all()
        assert rows == []


def test_forecaster_callback_produces_forecast_notification(
    session_maker: sessionmaker[Session],
) -> None:
    """When a forecaster is wired in, the handler writes kind=forecast rows."""
    household, pool = _setup_household(session_maker)
    today = date(2026, 4, 1)
    for i in range(35):
        _spend(
            session_maker,
            household_id=household.id,
            pool_id=pool.id,
            when=today - timedelta(days=34 - i),
            amount=Decimal("10.00"),
        )

    forecast_calls: list[tuple] = []

    async def fake_forecaster(request) -> str:
        forecast_calls.append(
            (
                request.household_id,
                request.pool_id,
                request.pool_name,
                request.pool_currency,
                len(request.series),
            )
        )
        return f"{request.pool_name} is on track."

    handler = make_daily_insights_handler(session_maker, forecaster=fake_forecaster)
    asyncio.run(
        handler(_make_job(household.id), _fixed_clock(datetime(2026, 4, 1, 12, 0, tzinfo=UTC)))
    )
    assert len(forecast_calls) == 1
    assert forecast_calls[0][2] == pool.name
    with session_maker() as s:
        from sqlalchemy import select

        rows = (
            s.execute(select(Notification).where(Notification.kind == "forecast")).scalars().all()
        )
        assert len(rows) == 1
        assert "on track" in rows[0].body


def test_forecaster_returning_none_writes_no_forecast(
    session_maker: sessionmaker[Session],
) -> None:
    """Forecaster returning None (e.g. AI unconfigured) silently skips the row."""
    household, pool = _setup_household(session_maker)
    today = date(2026, 4, 1)
    for i in range(35):
        _spend(
            session_maker,
            household_id=household.id,
            pool_id=pool.id,
            when=today - timedelta(days=34 - i),
            amount=Decimal("10.00"),
        )

    async def silent_forecaster(_request: object) -> None:
        return None

    handler = make_daily_insights_handler(session_maker, forecaster=silent_forecaster)
    asyncio.run(
        handler(_make_job(household.id), _fixed_clock(datetime(2026, 4, 1, 12, 0, tzinfo=UTC)))
    )
    with session_maker() as s:
        from sqlalchemy import select

        rows = (
            s.execute(select(Notification).where(Notification.kind == "forecast")).scalars().all()
        )
        assert rows == []


def test_spike_produces_anomaly_notification(
    session_maker: sessionmaker[Session],
) -> None:
    """A 10x spike on day 45 should flag at least one anomaly notification."""
    household, pool = _setup_household(session_maker)
    today = date(2026, 4, 1)
    # 30 days of alternating $10/$11 + 14 more days flat, then a $1000 spike today.
    for i in range(44):
        amt = Decimal("10") + Decimal(i % 2)
        _spend(
            session_maker,
            household_id=household.id,
            pool_id=pool.id,
            when=today - timedelta(days=44 - i),
            amount=amt,
        )
    _spend(
        session_maker,
        household_id=household.id,
        pool_id=pool.id,
        when=today,
        amount=Decimal("1000"),
    )
    handler = make_daily_insights_handler(session_maker)
    asyncio.run(
        handler(_make_job(household.id), _fixed_clock(datetime(2026, 4, 1, 12, 0, tzinfo=UTC)))
    )
    with session_maker() as s:
        from sqlalchemy import select

        rows = s.execute(select(Notification)).scalars().all()
        assert len(rows) >= 1
        anom = rows[0]
        assert anom.kind == "anomaly"
        assert anom.produced_by == "daily_insights"
        assert anom.entity_type == "envelope"
        assert anom.entity_id == pool.id
        # The spike was big enough to land in warning or critical.
        assert anom.severity in {"warning", "critical"}


# --- P6.5.c: sinking-fund forecasts --------------------------------------


def _setup_sinking_fund(
    session_maker: sessionmaker[Session],
    household: Household,
    *,
    name: str = "Roof",
    target_amount: Decimal = Decimal("5000"),
    target_date: date = date(2027, 1, 1),
) -> AllocationPool:
    """Seed an active sinking fund on ``household``; return its pool row."""
    pool_id = uuid4()
    with session_maker() as s:
        pool = AllocationPool(
            household_id=household.id,
            id=pool_id,
            name=name,
            pool_type=PoolType.SINKING_FUND,
            currency="USD",
            visibility="shared",
            is_active=True,
            is_system=False,
        )
        s.add(pool)
        s.flush()
        s.add(
            SinkingFund(
                household_id=household.id,
                pool_id=pool_id,
                target_amount=target_amount,
                target_date=target_date,
                contribution_strategy=ContributionStrategy.MANUAL,
                contribution_amount=None,
            )
        )
        s.commit()
        s.refresh(pool)
        return pool


def _contribute(
    session_maker: sessionmaker[Session],
    *,
    household_id,
    pool_id,
    when: date,
    amount: Decimal,
) -> None:
    """Insert one POSTED inflow shadow posting on the sinking fund's pool."""
    with session_maker() as s:
        tx_id = uuid4()
        s.add(
            ShadowTransaction(
                household_id=household_id,
                id=tx_id,
                date=when,
                description="contribution",
                reason=ShadowTxReason.REFILL,
                status=ShadowTxStatus.PENDING,
            )
        )
        s.flush()
        s.add_all(
            [
                ShadowPosting(
                    household_id=household_id,
                    id=uuid4(),
                    shadow_transaction_id=tx_id,
                    pool_id=pool_id,
                    amount=amount,
                    currency="USD",
                ),
                ShadowPosting(
                    household_id=household_id,
                    id=uuid4(),
                    shadow_transaction_id=tx_id,
                    pool_id=pool_id,
                    amount=-amount,
                    currency="USD",
                ),
            ]
        )
        s.flush()
        tx = s.get(ShadowTransaction, (household_id, tx_id))
        assert tx is not None
        tx.status = ShadowTxStatus.POSTED
        s.commit()


def test_sinking_fund_forecaster_receives_target_context(
    session_maker: sessionmaker[Session],
) -> None:
    """The handler's sinking-fund loop forwards target + balance + series."""
    household, _envelope = _setup_household(session_maker)
    sinking_pool = _setup_sinking_fund(
        session_maker,
        household,
        name="Vacation",
        target_amount=Decimal("3000"),
        target_date=date(2026, 12, 31),
    )
    today = date(2026, 5, 1)
    for i in range(3):
        _contribute(
            session_maker,
            household_id=household.id,
            pool_id=sinking_pool.id,
            when=today - timedelta(days=i * 10),
            amount=Decimal("200"),
        )

    requests: list[ForecastRequest] = []

    async def fake_forecaster(request: ForecastRequest) -> str:
        requests.append(request)
        if request.pool_kind == "sinking_fund":
            return f"{request.pool_name} on track for {request.target_date}."
        return f"{request.pool_name} (envelope) — no issues."

    handler = make_daily_insights_handler(session_maker, forecaster=fake_forecaster)
    asyncio.run(
        handler(
            _make_job(household.id),
            _fixed_clock(datetime(2026, 5, 1, 12, 0, tzinfo=UTC)),
        )
    )

    # Both pool kinds came through.
    kinds = [r.pool_kind for r in requests]
    assert kinds.count("envelope") == 1
    assert kinds.count("sinking_fund") == 1

    sf_request = next(r for r in requests if r.pool_kind == "sinking_fund")
    assert sf_request.pool_name == "Vacation"
    assert sf_request.target_amount == Decimal("3000")
    assert sf_request.target_date == date(2026, 12, 31)
    assert sf_request.current_balance == Decimal("0")  # net of balanced +/- pairs
    assert len(sf_request.series) == 60

    with session_maker() as s:
        from sqlalchemy import select

        rows = (
            s.execute(
                select(Notification).where(
                    Notification.kind == "forecast",
                    Notification.entity_type == "sinking_fund",
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].entity_id == sinking_pool.id
        assert "on track for" in rows[0].body


def test_sinking_fund_forecaster_returning_none_writes_no_row(
    session_maker: sessionmaker[Session],
) -> None:
    """Forecaster returning None for a sinking fund skips the notification."""
    household, _envelope = _setup_household(session_maker)
    _setup_sinking_fund(session_maker, household)

    async def silent_forecaster(_request: ForecastRequest) -> None:
        return None

    handler = make_daily_insights_handler(session_maker, forecaster=silent_forecaster)
    asyncio.run(
        handler(
            _make_job(household.id),
            _fixed_clock(datetime(2026, 5, 1, 12, 0, tzinfo=UTC)),
        )
    )
    with session_maker() as s:
        from sqlalchemy import select

        rows = (
            s.execute(select(Notification).where(Notification.entity_type == "sinking_fund"))
            .scalars()
            .all()
        )
        assert rows == []


def test_no_forecaster_no_sinking_fund_calls(
    session_maker: sessionmaker[Session],
) -> None:
    """Without a forecaster wired in, sinking funds produce no notifications."""
    household, _envelope = _setup_household(session_maker)
    _setup_sinking_fund(session_maker, household)
    handler = make_daily_insights_handler(session_maker)
    asyncio.run(
        handler(
            _make_job(household.id),
            _fixed_clock(datetime(2026, 5, 1, 12, 0, tzinfo=UTC)),
        )
    )
    with session_maker() as s:
        from sqlalchemy import select

        rows = (
            s.execute(select(Notification).where(Notification.entity_type == "sinking_fund"))
            .scalars()
            .all()
        )
        assert rows == []
