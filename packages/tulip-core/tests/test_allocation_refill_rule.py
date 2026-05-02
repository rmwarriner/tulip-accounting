"""Tests for the RefillRule structured value object."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_core.allocation import RefillRule, RefillStrategy
from tulip_core.money import Money


def test_fixed_amount_minimal() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250.00"), "USD"),
    )
    assert rule.amount is not None
    assert rule.amount.amount == Decimal("250.00")
    assert rule.percentage is None


def test_fixed_amount_requires_amount() -> None:
    with pytest.raises(ValueError, match="requires amount"):
        RefillRule(strategy=RefillStrategy.FIXED_AMOUNT)


def test_fixed_amount_forbids_percentage() -> None:
    with pytest.raises(ValueError, match="forbids percentage"):
        RefillRule(
            strategy=RefillStrategy.FIXED_AMOUNT,
            amount=Money(Decimal("250.00"), "USD"),
            percentage=Decimal("0.5"),
        )


def test_fixed_amount_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        RefillRule(
            strategy=RefillStrategy.FIXED_AMOUNT,
            amount=Money(Decimal("0.00"), "USD"),
        )


def test_fill_to_amount_minimal() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500.00"), "EUR"),
    )
    assert rule.amount is not None
    assert rule.amount.currency == "EUR"


def test_fill_to_amount_requires_amount() -> None:
    with pytest.raises(ValueError, match="requires amount"):
        RefillRule(strategy=RefillStrategy.FILL_TO_AMOUNT)


def test_percentage_of_income_minimal() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    assert rule.percentage == Decimal("0.10")
    assert rule.amount is None


def test_percentage_requires_percentage() -> None:
    with pytest.raises(ValueError, match="requires percentage"):
        RefillRule(strategy=RefillStrategy.PERCENTAGE_OF_INCOME)


def test_percentage_forbids_amount() -> None:
    with pytest.raises(ValueError, match="forbids amount"):
        RefillRule(
            strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
            amount=Money(Decimal("100"), "USD"),
            percentage=Decimal("0.10"),
        )


@pytest.mark.parametrize("p", [Decimal("0"), Decimal("-0.10"), Decimal("1.01"), Decimal("2")])
def test_percentage_out_of_range_rejected(p: Decimal) -> None:
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        RefillRule(strategy=RefillStrategy.PERCENTAGE_OF_INCOME, percentage=p)


def test_round_trip_fixed_amount() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250.00"), "USD"),
    )
    restored = RefillRule.from_dict(rule.to_dict())
    assert restored == rule


def test_round_trip_percentage() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.15"),
    )
    restored = RefillRule.from_dict(rule.to_dict())
    assert restored == rule


def test_to_dict_shape_is_jsonable_primitives() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250.00"), "USD"),
    )
    out = rule.to_dict()
    # Every value must be a JSON-safe primitive: str / int / float / bool / None.
    # No Money, no Decimal, no Enum.
    for k, v in out.items():
        assert isinstance(v, (str, int, float, bool, type(None))), (
            f"{k}={v!r} is not a JSON primitive"
        )
