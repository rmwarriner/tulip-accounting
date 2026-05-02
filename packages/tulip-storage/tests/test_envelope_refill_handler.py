"""Integration tests for the envelope_refill runner handler (P4.3.b)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from tulip_core.allocation import (
    RefillRule,
    RefillStrategy,
)
from tulip_core.allocation import (
    ShadowPosting as DomainShadowPosting,
)
from tulip_core.allocation import (
    ShadowTransaction as DomainShadowTransaction,
)
from tulip_core.allocation import (
    ShadowTxReason as DomainShadowTxReason,
)
from tulip_core.allocation import (
    ShadowTxStatus as DomainShadowTxStatus,
)
from tulip_core.money import Money
from tulip_storage.models import (
    AuditLog,
    BudgetPeriod,
    Household,
    PoolType,
    RolloverPolicy,
    ShadowTransaction,
)
from tulip_storage.repositories import (
    AllocationPoolRepository,
    EnvelopeRepository,
    ShadowTransactionRepository,
)
from tulip_storage.runner import Runner
from tulip_storage.runner.handlers import make_envelope_refill_handler
from tulip_storage.runner.handlers.envelope_refill import EnvelopeRefillError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


# ---- Fixtures ---------------------------------------------------------


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


class FakeClock:
    def __init__(self, *, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _seed_envelope(
    session: Session,
    household_id: UUID,
    *,
    name: str,
    refill_rule: RefillRule | None,
    currency: str = "USD",
) -> UUID:
    repo = EnvelopeRepository(session, household_id)
    pool, _env = repo.create(
        name=name,
        currency=currency,
        budget_period=BudgetPeriod.MONTHLY,
        rollover_policy=RolloverPolicy.RESET,
        refill_rule=refill_rule.to_dict() if refill_rule is not None else None,
    )
    session.commit()
    return pool.id


def _seed_inflow(
    session: Session,
    household_id: UUID,
    *,
    amount: Decimal,
    currency: str,
    when: date,
) -> None:
    """Post a BUDGET_INFLOW shadow tx so PERCENTAGE_OF_INCOME has data."""
    pool_repo = AllocationPoolRepository(session, household_id)
    sys_pools = pool_repo.get_or_create_system_pools(currency=currency)
    inflow_pool = sys_pools[PoolType.INFLOW]
    unallocated_pool = sys_pools[PoolType.UNALLOCATED]
    domain_tx = DomainShadowTransaction(
        id=uuid4(),
        household_id=household_id,
        date=when,
        description="Test inflow",
        reason=DomainShadowTxReason.BUDGET_INFLOW,
        postings=(
            DomainShadowPosting(
                id=uuid4(),
                pool_id=inflow_pool.id,
                amount=Money(-amount, currency),
            ),
            DomainShadowPosting(
                id=uuid4(),
                pool_id=unallocated_pool.id,
                amount=Money(amount, currency),
            ),
        ),
        status=DomainShadowTxStatus.POSTED,
    )
    ShadowTransactionRepository(session, household_id).save_balanced(domain_tx)
    session.commit()


def _make_runner_with_handler(session_maker: sessionmaker[Session], *, clock: FakeClock) -> Runner:
    runner = Runner(session_maker, clock=clock, poll_interval_seconds=0.01)
    runner.register_handler("envelope_refill", make_envelope_refill_handler(session_maker))
    return runner


def _envelope_balance(
    session: Session, household_id: UUID, envelope_id: UUID, currency: str
) -> Decimal:
    return (
        ShadowTransactionRepository(session, household_id)
        .balance_for_pool(envelope_id, currency=currency)
        .get(currency, Decimal(0))
    )


# ---- FIXED_AMOUNT happy path -----------------------------------------


@pytest.mark.asyncio
async def test_fixed_amount_refill_posts_shadow_tx(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Groceries", refill_rule=rule)

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)

    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    fired = await runner.run_once()
    assert fired == 1

    # Envelope balance grew by 250.
    session.expire_all()
    balance = _envelope_balance(session, household.id, envelope_id, "USD")
    assert balance == Decimal("250")


# ---- FILL_TO_AMOUNT semantics ----------------------------------------


@pytest.mark.asyncio
async def test_fill_to_amount_tops_up_only_the_gap(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Rent", refill_rule=rule)

    # Pre-seed envelope with 200 via a manual refill from Unallocated.
    pool_repo = AllocationPoolRepository(session, household.id)
    unallocated = pool_repo.get_or_create_system_pools(currency="USD")[PoolType.UNALLOCATED]
    seed_tx = DomainShadowTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 5, 15),
        description="Pre-seed",
        reason=DomainShadowTxReason.REFILL,
        postings=(
            DomainShadowPosting(
                id=uuid4(),
                pool_id=unallocated.id,
                amount=Money(Decimal("-200"), "USD"),
            ),
            DomainShadowPosting(
                id=uuid4(),
                pool_id=envelope_id,
                amount=Money(Decimal("200"), "USD"),
            ),
        ),
        status=DomainShadowTxStatus.POSTED,
    )
    ShadowTransactionRepository(session, household.id).save_balanced(seed_tx)
    session.commit()

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    await runner.run_once()

    session.expire_all()
    balance = _envelope_balance(session, household.id, envelope_id, "USD")
    assert balance == Decimal("500")  # filled to target


@pytest.mark.asyncio
async def test_fill_to_amount_skips_when_at_target(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Rent", refill_rule=rule)
    # Pre-seed envelope at exactly target.
    pool_repo = AllocationPoolRepository(session, household.id)
    unallocated = pool_repo.get_or_create_system_pools(currency="USD")[PoolType.UNALLOCATED]
    ShadowTransactionRepository(session, household.id).save_balanced(
        DomainShadowTransaction(
            id=uuid4(),
            household_id=household.id,
            date=date(2026, 5, 15),
            description="Pre-seed at target",
            reason=DomainShadowTxReason.REFILL,
            postings=(
                DomainShadowPosting(
                    id=uuid4(),
                    pool_id=unallocated.id,
                    amount=Money(Decimal("-500"), "USD"),
                ),
                DomainShadowPosting(
                    id=uuid4(),
                    pool_id=envelope_id,
                    amount=Money(Decimal("500"), "USD"),
                ),
            ),
            status=DomainShadowTxStatus.POSTED,
        )
    )
    session.commit()

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    await runner.run_once()

    session.expire_all()
    balance = _envelope_balance(session, household.id, envelope_id, "USD")
    assert balance == Decimal("500")  # unchanged

    # No new REFILL shadow tx posted (only the pre-seed exists).
    refill_count = (
        session.execute(
            select(ShadowTransaction).where(
                ShadowTransaction.household_id == household.id,
                ShadowTransaction.description == "Auto-refill: Rent",
            )
        )
        .scalars()
        .all()
    )
    assert refill_count == []


# ---- PERCENTAGE_OF_INCOME --------------------------------------------


@pytest.mark.asyncio
async def test_percentage_of_income_uses_recent_inflow(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Savings", refill_rule=rule)
    # 3000 USD inflow within the 30-day lookback window.
    _seed_inflow(
        session,
        household.id,
        amount=Decimal("3000"),
        currency="USD",
        when=date(2026, 5, 28),
    )

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    await runner.run_once()

    session.expire_all()
    balance = _envelope_balance(session, household.id, envelope_id, "USD")
    assert balance == Decimal("300.00")  # 10% of 3000


@pytest.mark.asyncio
async def test_percentage_of_income_zero_when_no_inflow(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Savings", refill_rule=rule)

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    await runner.run_once()

    session.expire_all()
    balance = _envelope_balance(session, household.id, envelope_id, "USD")
    assert balance == Decimal("0")


# ---- No-op cases -----------------------------------------------------


@pytest.mark.asyncio
async def test_envelope_with_no_refill_rule_is_silent_no_op(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    envelope_id = _seed_envelope(session, household.id, name="No rule", refill_rule=None)

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    fired = await runner.run_once()
    assert fired == 1

    session.expire_all()
    assert _envelope_balance(session, household.id, envelope_id, "USD") == Decimal("0")


@pytest.mark.asyncio
async def test_inactive_envelope_is_silent_no_op(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Dead", refill_rule=rule)
    AllocationPoolRepository(session, household.id).deactivate(envelope_id)
    session.commit()

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    fired = await runner.run_once()
    assert fired == 1

    session.expire_all()
    assert _envelope_balance(session, household.id, envelope_id, "USD") == Decimal("0")


@pytest.mark.asyncio
async def test_unknown_envelope_raises_envelope_refill_error(
    session_maker: sessionmaker[Session],
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(uuid4())},  # nonexistent
        fire_at=t0,
    )
    # The runner catches the handler exception → fails the run, schedules retry.
    await runner.run_once()
    # No exception bubbles out; the runner records the failure.


@pytest.mark.asyncio
async def test_payload_missing_envelope_id_raises(
    session_maker: sessionmaker[Session],
    household: Household,
) -> None:
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    handler = make_envelope_refill_handler(session_maker)

    from tulip_storage.models import ScheduledJob

    bogus_job = ScheduledJob(
        household_id=household.id,
        id=uuid4(),
        kind="envelope_refill",
        payload={"wrong_key": "x"},
        rrule=None,
        dtstart=t0,
        next_run_at=t0,
        idempotency_key=None,
        is_active=True,
        created_by_user_id=None,
    )
    with pytest.raises(EnvelopeRefillError, match="envelope_id"):
        await handler(bogus_job, clock)


# ---- Audit log -------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_row_written_with_actor_kind_system(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Groceries", refill_rule=rule)

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    await runner.run_once()

    session.expire_all()
    audit_rows = list(
        session.execute(
            select(AuditLog).where(
                AuditLog.household_id == household.id,
                AuditLog.entity_type == "shadow_transaction",
                AuditLog.action == "create",
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.actor_kind == "system"
    assert row.actor_user_id is None
    after = row.after_snapshot or {}
    assert after.get("reason") == "refill"
    assert after.get("envelope_id") == str(envelope_id)
    assert after.get("amount") == "250"
    assert after.get("rule_strategy") == "fixed_amount"


# ---- Recurring schedule end-to-end -----------------------------------


@pytest.mark.asyncio
async def test_recurring_refill_fires_monthly(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("100"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Groceries", refill_rule=rule)

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_recurring(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        rrule="FREQ=MONTHLY",
        start_at=t0,
    )

    # Fire month 1.
    await runner.run_once()
    session.expire_all()
    assert _envelope_balance(session, household.id, envelope_id, "USD") == Decimal("100")

    # Advance 1 month and fire again.
    clock.advance(timedelta(days=31))
    await runner.run_once()
    session.expire_all()
    assert _envelope_balance(session, household.id, envelope_id, "USD") == Decimal("200")


# ---- Loop drives the full pipeline -----------------------------------


@pytest.mark.asyncio
async def test_runner_loop_fires_envelope_refill_end_to_end(
    session_maker: sessionmaker[Session],
    session: Session,
    household: Household,
) -> None:
    """The full async loop (start/stop) materializes a refill."""
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("75"), "USD"),
    )
    envelope_id = _seed_envelope(session, household.id, name="Coffee", refill_rule=rule)

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now=t0)
    runner = _make_runner_with_handler(session_maker, clock=clock)
    runner.schedule_one(
        household_id=household.id,
        kind="envelope_refill",
        payload={"envelope_id": str(envelope_id)},
        fire_at=t0,
    )
    await runner.start()
    await asyncio.sleep(0.05)  # one poll tick
    await runner.stop()

    session.expire_all()
    assert _envelope_balance(session, household.id, envelope_id, "USD") == Decimal("75")
