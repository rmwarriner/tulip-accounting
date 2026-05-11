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
)
from tulip_storage.runner.handlers import make_daily_insights_handler


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
