"""Tests for the daily series helpers on ShadowTransactionRepository (P6.5.c)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_storage.models import (
    AllocationPool,
    Household,
    PoolType,
    ShadowPosting,
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
)
from tulip_storage.repositories import ShadowTransactionRepository


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


@pytest.fixture
def pool(session: Session, household: Household) -> AllocationPool:
    p = AllocationPool(
        household_id=household.id,
        id=uuid4(),
        pool_type=PoolType.SINKING_FUND,
        name="Roof",
        currency="USD",
        is_active=True,
        is_system=False,
    )
    session.add(p)
    session.commit()
    return p


def _post(
    session: Session,
    *,
    household: Household,
    pool: AllocationPool,
    when: date,
    amount: Decimal,
    status: ShadowTxStatus = ShadowTxStatus.POSTED,
) -> None:
    """Insert a balanced shadow transaction with one ``amount``-signed posting on ``pool``."""
    tx_id = uuid4()
    session.add(
        ShadowTransaction(
            household_id=household.id,
            id=tx_id,
            date=when,
            description="seed",
            reason=ShadowTxReason.REFILL,
            status=ShadowTxStatus.PENDING,
        )
    )
    session.flush()
    session.add_all(
        [
            ShadowPosting(
                household_id=household.id,
                id=uuid4(),
                shadow_transaction_id=tx_id,
                pool_id=pool.id,
                amount=amount,
                currency=pool.currency,
            ),
            ShadowPosting(
                household_id=household.id,
                id=uuid4(),
                shadow_transaction_id=tx_id,
                pool_id=pool.id,
                amount=-amount,
                currency=pool.currency,
            ),
        ]
    )
    session.flush()
    tx = session.get(ShadowTransaction, (household.id, tx_id))
    assert tx is not None
    tx.status = status
    session.commit()


class TestDailyContributionSeriesForPool:
    """``daily_contribution_series_for_pool`` returns daily inflow totals (P6.5.c)."""

    def test_only_positive_postings_count(
        self, session: Session, household: Household, pool: AllocationPool
    ) -> None:
        _post(session, household=household, pool=pool, when=date(2026, 5, 1), amount=Decimal("100"))
        repo = ShadowTransactionRepository(session, household.id)
        series = repo.daily_contribution_series_for_pool(
            pool.id,
            currency="USD",
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 1),
        )
        # The balanced -amount posting must NOT count toward contributions.
        assert series == {date(2026, 5, 1): Decimal("100")}

    def test_sparse_return_zero_for_missing_dates(
        self, session: Session, household: Household, pool: AllocationPool
    ) -> None:
        _post(session, household=household, pool=pool, when=date(2026, 5, 3), amount=Decimal("50"))
        repo = ShadowTransactionRepository(session, household.id)
        series = repo.daily_contribution_series_for_pool(
            pool.id,
            currency="USD",
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 5),
        )
        # Sparse: only the date with a contribution is keyed.
        assert series == {date(2026, 5, 3): Decimal("50")}

    def test_voided_transactions_excluded(
        self, session: Session, household: Household, pool: AllocationPool
    ) -> None:
        _post(
            session,
            household=household,
            pool=pool,
            when=date(2026, 5, 2),
            amount=Decimal("75"),
            status=ShadowTxStatus.VOIDED,
        )
        repo = ShadowTransactionRepository(session, household.id)
        series = repo.daily_contribution_series_for_pool(
            pool.id,
            currency="USD",
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 5),
        )
        assert series == {}

    def test_currency_filter(
        self, session: Session, household: Household, pool: AllocationPool
    ) -> None:
        _post(session, household=household, pool=pool, when=date(2026, 5, 1), amount=Decimal("100"))
        repo = ShadowTransactionRepository(session, household.id)
        # Pool is USD; asking for EUR returns empty.
        series = repo.daily_contribution_series_for_pool(
            pool.id,
            currency="EUR",
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 5),
        )
        assert series == {}

    def test_date_range_filter(
        self, session: Session, household: Household, pool: AllocationPool
    ) -> None:
        _post(
            session, household=household, pool=pool, when=date(2026, 4, 15), amount=Decimal("100")
        )
        _post(
            session, household=household, pool=pool, when=date(2026, 5, 15), amount=Decimal("200")
        )
        repo = ShadowTransactionRepository(session, household.id)
        series = repo.daily_contribution_series_for_pool(
            pool.id,
            currency="USD",
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 31),
        )
        assert series == {date(2026, 5, 15): Decimal("200")}
