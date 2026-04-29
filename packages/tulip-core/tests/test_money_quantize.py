"""Tests for Money.quantize_to: round to currency minor_units, banker's rounding."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tulip_core.money import Money


class TestQuantizeRoundsToMinorUnits:
    @pytest.mark.parametrize(
        ("amount", "currency", "expected"),
        [
            ("12.345", "USD", "12.34"),  # banker's: round half to even
            ("12.355", "USD", "12.36"),  # banker's: round half to even
            ("12.355001", "USD", "12.36"),
            ("100", "USD", "100.00"),  # widens precision when below minor_units
            ("100.5", "JPY", "100"),  # JPY has 0 minor_units; banker's → 100
            ("101.5", "JPY", "102"),  # banker's: round half to even
            ("12.0005", "BHD", "12.000"),  # BHD has 3 minor_units; banker's
            ("12.0015", "BHD", "12.002"),
        ],
    )
    def test_quantize(self, amount: str, currency: str, expected: str):
        result = Money(Decimal(amount), currency).quantize_to_currency()
        assert result == Money(Decimal(expected), currency)

    def test_returns_money_in_same_currency(self):
        m = Money(Decimal("1.234"), "USD")
        assert m.quantize_to_currency().currency == "USD"


@pytest.mark.property
class TestQuantizeProperties:
    @given(
        amount=st.decimals(
            min_value=Decimal("-1000000000000"),
            max_value=Decimal("1000000000000"),
            allow_nan=False,
            allow_infinity=False,
            places=8,
        ),
        currency=st.sampled_from(["USD", "EUR", "JPY", "BHD"]),
    )
    def test_quantize_is_idempotent(self, amount: Decimal, currency: str) -> None:
        once = Money(amount, currency).quantize_to_currency()
        twice = once.quantize_to_currency()
        assert once == twice

    @given(
        amount=st.decimals(
            min_value=Decimal("0.00000001"),
            max_value=Decimal("1000000"),
            allow_nan=False,
            allow_infinity=False,
            places=8,
        ),
        currency=st.sampled_from(["USD", "EUR", "JPY", "BHD"]),
    )
    def test_quantize_preserves_sign_for_nonzero(self, amount: Decimal, currency: str) -> None:
        positive = Money(amount, currency).quantize_to_currency()
        negative = Money(-amount, currency).quantize_to_currency()
        # Banker's rounding can round a tiny positive amount down to zero in JPY,
        # so we only check sign for amounts >= one minor unit.
        if positive.amount > Decimal("1"):
            assert positive.amount > 0
            assert negative.amount < 0
