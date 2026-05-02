"""Unit tests for evaluate_refill_rule (P4.3.b)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_core.allocation import (
    RefillRule,
    RefillStrategy,
    evaluate_refill_rule,
)
from tulip_core.money import CurrencyMismatchError, Money

# ---- FIXED_AMOUNT --------------------------------------------------


def test_fixed_amount_returns_rule_amount() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250"), "USD"),
    )
    result = evaluate_refill_rule(rule, current_balance=Money(Decimal("0"), "USD"))
    assert result == Money(Decimal("250"), "USD")


def test_fixed_amount_ignores_current_balance() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250"), "USD"),
    )
    # Even if envelope already has plenty, fixed contributes the same.
    result = evaluate_refill_rule(rule, current_balance=Money(Decimal("1000"), "USD"))
    assert result == Money(Decimal("250"), "USD")


def test_fixed_amount_currency_mismatch_raises() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FIXED_AMOUNT,
        amount=Money(Decimal("250"), "USD"),
    )
    with pytest.raises(CurrencyMismatchError):
        evaluate_refill_rule(rule, current_balance=Money(Decimal("0"), "EUR"))


# ---- FILL_TO_AMOUNT ------------------------------------------------


def test_fill_to_amount_returns_gap() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    result = evaluate_refill_rule(rule, current_balance=Money(Decimal("200"), "USD"))
    assert result == Money(Decimal("300"), "USD")


def test_fill_to_amount_zero_when_at_target() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    result = evaluate_refill_rule(rule, current_balance=Money(Decimal("500"), "USD"))
    assert result == Money.zero("USD")


def test_fill_to_amount_zero_when_above_target() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    result = evaluate_refill_rule(rule, current_balance=Money(Decimal("750"), "USD"))
    assert result == Money.zero("USD")


def test_fill_to_amount_full_when_negative_balance() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    # Negative balance is permitted (over-spent envelope). Top-up fills
    # the gap including the deficit.
    result = evaluate_refill_rule(rule, current_balance=Money(Decimal("-50"), "USD"))
    assert result == Money(Decimal("550"), "USD")


def test_fill_to_amount_currency_mismatch_raises() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.FILL_TO_AMOUNT,
        amount=Money(Decimal("500"), "USD"),
    )
    with pytest.raises(CurrencyMismatchError):
        evaluate_refill_rule(rule, current_balance=Money(Decimal("0"), "EUR"))


# ---- PERCENTAGE_OF_INCOME ------------------------------------------


def test_percentage_returns_pct_of_inflow() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    result = evaluate_refill_rule(
        rule,
        current_balance=Money(Decimal("0"), "USD"),
        recent_inflow=Money(Decimal("3000"), "USD"),
    )
    assert result == Money(Decimal("300.00"), "USD")


def test_percentage_returns_zero_with_no_inflow() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    result = evaluate_refill_rule(
        rule, current_balance=Money(Decimal("0"), "USD"), recent_inflow=None
    )
    assert result == Money.zero("USD")


def test_percentage_returns_zero_with_zero_inflow() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    result = evaluate_refill_rule(
        rule,
        current_balance=Money(Decimal("0"), "USD"),
        recent_inflow=Money(Decimal("0"), "USD"),
    )
    assert result == Money.zero("USD")


def test_percentage_returns_zero_with_negative_inflow() -> None:
    # Negative inflow shouldn't happen in practice (it would be a refund
    # shape), but the engine should be robust to it.
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    result = evaluate_refill_rule(
        rule,
        current_balance=Money(Decimal("0"), "USD"),
        recent_inflow=Money(Decimal("-100"), "USD"),
    )
    assert result == Money.zero("USD")


def test_percentage_currency_mismatch_raises() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("0.10"),
    )
    with pytest.raises(CurrencyMismatchError):
        evaluate_refill_rule(
            rule,
            current_balance=Money(Decimal("0"), "USD"),
            recent_inflow=Money(Decimal("3000"), "EUR"),
        )


def test_percentage_full_inflow_at_100_percent() -> None:
    rule = RefillRule(
        strategy=RefillStrategy.PERCENTAGE_OF_INCOME,
        percentage=Decimal("1.0"),
    )
    result = evaluate_refill_rule(
        rule,
        current_balance=Money(Decimal("0"), "USD"),
        recent_inflow=Money(Decimal("500"), "USD"),
    )
    assert result == Money(Decimal("500.0"), "USD")
