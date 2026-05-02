"""Tests for the SinkingFund value object."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.allocation import ContributionStrategy, Pool, PoolType, SinkingFund
from tulip_core.money import Money


def _sf_pool(currency: str = "USD") -> Pool:
    return Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.SINKING_FUND,
        name="Vacation",
        currency=currency,
    )


def test_manual_minimal() -> None:
    sf = SinkingFund(
        pool=_sf_pool(),
        target_amount=Money(Decimal("3000"), "USD"),
        target_date=date(2027, 6, 1),
        contribution_strategy=ContributionStrategy.MANUAL,
    )
    assert sf.contribution_amount is None


def test_manual_with_contribution_amount_allowed() -> None:
    sf = SinkingFund(
        pool=_sf_pool(),
        target_amount=Money(Decimal("3000"), "USD"),
        target_date=date(2027, 6, 1),
        contribution_strategy=ContributionStrategy.MANUAL,
        contribution_amount=Money(Decimal("250"), "USD"),
    )
    assert sf.contribution_amount is not None
    assert sf.contribution_amount.amount == Decimal("250")


def test_even_split_forbids_explicit_contribution_amount() -> None:
    with pytest.raises(ValueError, match="EVEN_SPLIT"):
        SinkingFund(
            pool=_sf_pool(),
            target_amount=Money(Decimal("3000"), "USD"),
            target_date=date(2027, 6, 1),
            contribution_strategy=ContributionStrategy.EVEN_SPLIT,
            contribution_amount=Money(Decimal("250"), "USD"),
        )


def test_percentage_of_income_forbids_explicit_contribution_amount() -> None:
    with pytest.raises(ValueError, match="PERCENTAGE_OF_INCOME"):
        SinkingFund(
            pool=_sf_pool(),
            target_amount=Money(Decimal("3000"), "USD"),
            target_date=date(2027, 6, 1),
            contribution_strategy=ContributionStrategy.PERCENTAGE_OF_INCOME,
            contribution_amount=Money(Decimal("250"), "USD"),
        )


def test_rejects_non_sinking_fund_pool() -> None:
    env_pool = Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
    )
    with pytest.raises(ValueError, match="type 'sinking_fund'"):
        SinkingFund(
            pool=env_pool,
            target_amount=Money(Decimal("3000"), "USD"),
            target_date=date(2027, 6, 1),
            contribution_strategy=ContributionStrategy.MANUAL,
        )


def test_target_currency_must_match_pool() -> None:
    pool = _sf_pool("USD")
    with pytest.raises(ValueError, match="target_amount currency"):
        SinkingFund(
            pool=pool,
            target_amount=Money(Decimal("3000"), "EUR"),
            target_date=date(2027, 6, 1),
            contribution_strategy=ContributionStrategy.MANUAL,
        )


def test_target_amount_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        SinkingFund(
            pool=_sf_pool(),
            target_amount=Money(Decimal("0"), "USD"),
            target_date=date(2027, 6, 1),
            contribution_strategy=ContributionStrategy.MANUAL,
        )


def test_contribution_currency_must_match_pool() -> None:
    pool = _sf_pool("USD")
    with pytest.raises(ValueError, match="contribution_amount currency"):
        SinkingFund(
            pool=pool,
            target_amount=Money(Decimal("3000"), "USD"),
            target_date=date(2027, 6, 1),
            contribution_strategy=ContributionStrategy.MANUAL,
            contribution_amount=Money(Decimal("250"), "EUR"),
        )
