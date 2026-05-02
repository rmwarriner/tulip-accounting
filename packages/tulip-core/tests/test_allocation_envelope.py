"""Tests for the Envelope value object."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.allocation import (
    BudgetPeriod,
    Envelope,
    Pool,
    PoolType,
    RefillRule,
    RefillStrategy,
    RolloverPolicy,
)
from tulip_core.money import Money


def _envelope_pool(currency: str = "USD") -> Pool:
    return Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency=currency,
    )


def test_envelope_minimal() -> None:
    env = Envelope(
        pool=_envelope_pool(),
        budget_period=BudgetPeriod.MONTHLY,
        rollover_policy=RolloverPolicy.RESET,
    )
    assert env.budget_amount is None
    assert env.refill_rule is None


def test_envelope_with_budget_and_refill() -> None:
    pool = _envelope_pool("USD")
    env = Envelope(
        pool=pool,
        budget_period=BudgetPeriod.MONTHLY,
        rollover_policy=RolloverPolicy.ACCUMULATE,
        budget_amount=Money(Decimal("400"), "USD"),
        refill_rule=RefillRule(
            strategy=RefillStrategy.FIXED_AMOUNT,
            amount=Money(Decimal("100"), "USD"),
        ),
    )
    assert env.budget_amount is not None
    assert env.refill_rule is not None


def test_envelope_rejects_non_envelope_pool() -> None:
    sf_pool = Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.SINKING_FUND,
        name="Vacation",
        currency="USD",
    )
    with pytest.raises(ValueError, match="type 'envelope'"):
        Envelope(
            pool=sf_pool,
            budget_period=BudgetPeriod.MONTHLY,
            rollover_policy=RolloverPolicy.RESET,
        )


def test_envelope_budget_currency_must_match_pool() -> None:
    pool = _envelope_pool("USD")
    with pytest.raises(ValueError, match="budget_amount currency"):
        Envelope(
            pool=pool,
            budget_period=BudgetPeriod.MONTHLY,
            rollover_policy=RolloverPolicy.RESET,
            budget_amount=Money(Decimal("400"), "EUR"),
        )


def test_envelope_budget_must_be_non_negative() -> None:
    pool = _envelope_pool("USD")
    with pytest.raises(ValueError, match="non-negative"):
        Envelope(
            pool=pool,
            budget_period=BudgetPeriod.MONTHLY,
            rollover_policy=RolloverPolicy.RESET,
            budget_amount=Money(Decimal("-1"), "USD"),
        )


def test_envelope_refill_currency_must_match_pool() -> None:
    pool = _envelope_pool("USD")
    with pytest.raises(ValueError, match=r"refill_rule\.amount currency"):
        Envelope(
            pool=pool,
            budget_period=BudgetPeriod.MONTHLY,
            rollover_policy=RolloverPolicy.RESET,
            refill_rule=RefillRule(
                strategy=RefillStrategy.FIXED_AMOUNT,
                amount=Money(Decimal("100"), "EUR"),
            ),
        )


def test_envelope_zero_budget_allowed() -> None:
    pool = _envelope_pool("USD")
    env = Envelope(
        pool=pool,
        budget_period=BudgetPeriod.MONTHLY,
        rollover_policy=RolloverPolicy.RESET,
        budget_amount=Money(Decimal("0"), "USD"),
    )
    assert env.budget_amount is not None
    assert env.budget_amount.amount == Decimal("0")
