"""Property-based tests for Money arithmetic invariants.

These tests are the foundation invariant of the whole accounting system.
They use hypothesis to generate Decimal amounts within sensible bounds
matching the schema in ARCHITECTURE.md §4.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tulip_core.money import Money

MONEY_MAX = Decimal("1000000000000")  # 1e12
MONEY_FRACTIONAL_PLACES = 8


def decimals_in_money_range() -> st.SearchStrategy[Decimal]:
    """Generate Decimal values within Tulip's monetary range and precision."""
    return st.decimals(
        min_value=-MONEY_MAX,
        max_value=MONEY_MAX,
        allow_nan=False,
        allow_infinity=False,
        places=MONEY_FRACTIONAL_PLACES,
    )


def money_usd() -> st.SearchStrategy[Money]:
    return decimals_in_money_range().map(lambda d: Money(d, "USD"))


@pytest.mark.property
class TestMoneyArithmeticProperties:
    @given(a=money_usd(), b=money_usd())
    def test_addition_is_commutative(self, a: Money, b: Money) -> None:
        assert a + b == b + a

    @given(a=money_usd(), b=money_usd(), c=money_usd())
    def test_addition_is_associative(self, a: Money, b: Money, c: Money) -> None:
        assert (a + b) + c == a + (b + c)

    @given(a=money_usd())
    def test_zero_is_additive_identity(self, a: Money) -> None:
        assert a + Money.zero("USD") == a

    @given(a=money_usd())
    def test_negation_is_additive_inverse(self, a: Money) -> None:
        assert a + (-a) == Money.zero("USD")

    @given(
        a=money_usd(),
        n=st.integers(min_value=1, max_value=10000),
    )
    def test_multiply_then_divide_round_trips(self, a: Money, n: int) -> None:
        # (a * n) / n == a — Decimal-exact, since (a.amount * n) / n == a.amount.
        scaled = a * n
        round_tripped = Money(scaled.amount / Decimal(n), a.currency)
        assert round_tripped == a
