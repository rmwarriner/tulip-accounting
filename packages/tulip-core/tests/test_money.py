"""Unit tests for Money value object."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from tulip_core.money import CurrencyMismatchError, Money


class TestMoneyConstruction:
    def test_constructs_from_decimal_and_currency(self):
        m = Money(Decimal("10.00"), "USD")
        assert m.amount == Decimal("10.00")
        assert m.currency == "USD"

    def test_float_amount_raises(self):
        with pytest.raises(TypeError):
            Money(10.00, "USD")  # type: ignore[arg-type]

    def test_unknown_currency_raises(self):
        with pytest.raises(ValueError, match="currency"):
            Money(Decimal("10.00"), "XYZ")

    def test_is_immutable(self):
        m = Money(Decimal("10.00"), "USD")
        with pytest.raises(FrozenInstanceError):
            m.amount = Decimal("20.00")  # type: ignore[misc]


class TestMoneyEquality:
    def test_equal_when_amount_and_currency_match(self):
        assert Money(Decimal("10.00"), "USD") == Money(Decimal("10.00"), "USD")

    def test_not_equal_when_currencies_differ(self):
        assert Money(Decimal("10.00"), "USD") != Money(Decimal("10.00"), "EUR")

    def test_not_equal_when_amounts_differ(self):
        assert Money(Decimal("10.00"), "USD") != Money(Decimal("11.00"), "USD")


class TestMoneyArithmetic:
    def test_add_same_currency(self):
        result = Money(Decimal("3.50"), "USD") + Money(Decimal("1.25"), "USD")
        assert result == Money(Decimal("4.75"), "USD")

    def test_add_different_currencies_raises(self):
        with pytest.raises(CurrencyMismatchError):
            _ = Money(Decimal("1.00"), "USD") + Money(Decimal("1.00"), "EUR")

    def test_subtract_same_currency(self):
        result = Money(Decimal("10.00"), "USD") - Money(Decimal("3.25"), "USD")
        assert result == Money(Decimal("6.75"), "USD")

    def test_subtract_different_currencies_raises(self):
        with pytest.raises(CurrencyMismatchError):
            _ = Money(Decimal("1.00"), "USD") - Money(Decimal("1.00"), "EUR")

    def test_negation(self):
        assert -Money(Decimal("5.00"), "USD") == Money(Decimal("-5.00"), "USD")
        assert -Money(Decimal("0"), "USD") == Money(Decimal("0"), "USD")

    def test_multiply_by_int(self):
        assert Money(Decimal("2.50"), "USD") * 4 == Money(Decimal("10.00"), "USD")

    def test_multiply_by_decimal(self):
        assert Money(Decimal("10.00"), "USD") * Decimal("0.5") == Money(Decimal("5.00"), "USD")

    def test_multiply_by_money_raises(self):
        with pytest.raises(TypeError):
            _ = Money(Decimal("2.00"), "USD") * Money(Decimal("3.00"), "USD")  # type: ignore[operator]

    def test_multiply_by_float_raises(self):
        with pytest.raises(TypeError):
            _ = Money(Decimal("2.00"), "USD") * 1.5  # type: ignore[operator]


class TestMoneyZero:
    def test_zero_returns_zero_amount_money(self):
        z = Money.zero("USD")
        assert z.amount == Decimal("0")
        assert z.currency == "USD"

    def test_zero_unknown_currency_raises(self):
        with pytest.raises(ValueError, match="currency"):
            Money.zero("XYZ")


class TestMoneyRepr:
    def test_repr_contains_amount_and_currency(self):
        r = repr(Money(Decimal("87.42"), "USD"))
        assert "87.42" in r
        assert "USD" in r
        assert "Money" in r
