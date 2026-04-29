"""Unit tests for Posting value object."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.money import Money
from tulip_core.transactions import Posting


class TestPostingConstruction:
    def test_minimal_posting(self):
        p = Posting(
            id=uuid4(),
            account_id=uuid4(),
            amount=Money(Decimal("10.00"), "USD"),
        )
        assert p.amount == Money(Decimal("10.00"), "USD")
        assert p.pool_id is None
        assert p.memo is None
        assert p.fx_rate is None
        assert p.fx_amount is None

    def test_with_pool_and_memo(self):
        pool = uuid4()
        p = Posting(
            id=uuid4(),
            account_id=uuid4(),
            amount=Money(Decimal("-5.00"), "USD"),
            pool_id=pool,
            memo="Coffee",
        )
        assert p.pool_id == pool
        assert p.memo == "Coffee"

    def test_with_fx(self):
        # EUR-denominated posting against a USD account, with explicit FX.
        p = Posting(
            id=uuid4(),
            account_id=uuid4(),
            amount=Money(Decimal("-100.00"), "EUR"),
            fx_rate=Decimal("1.10"),
            fx_amount=Money(Decimal("-110.00"), "USD"),
        )
        assert p.fx_rate == Decimal("1.10")
        assert p.fx_amount == Money(Decimal("-110.00"), "USD")

    def test_immutable(self):
        p = Posting(
            id=uuid4(),
            account_id=uuid4(),
            amount=Money(Decimal("10.00"), "USD"),
        )
        with pytest.raises(FrozenInstanceError):
            p.memo = "x"  # type: ignore[misc]


class TestPostingFXValidation:
    def test_fx_rate_without_fx_amount_raises(self):
        with pytest.raises(ValueError, match="fx"):
            Posting(
                id=uuid4(),
                account_id=uuid4(),
                amount=Money(Decimal("100.00"), "EUR"),
                fx_rate=Decimal("1.10"),
                fx_amount=None,
            )

    def test_fx_amount_without_fx_rate_raises(self):
        with pytest.raises(ValueError, match="fx"):
            Posting(
                id=uuid4(),
                account_id=uuid4(),
                amount=Money(Decimal("100.00"), "EUR"),
                fx_rate=None,
                fx_amount=Money(Decimal("110.00"), "USD"),
            )

    def test_negative_fx_rate_raises(self):
        with pytest.raises(ValueError, match="fx_rate"):
            Posting(
                id=uuid4(),
                account_id=uuid4(),
                amount=Money(Decimal("100.00"), "EUR"),
                fx_rate=Decimal("-1.10"),
                fx_amount=Money(Decimal("-110.00"), "USD"),
            )

    def test_fx_amount_same_currency_as_posting_raises(self):
        # If fx_amount is the same currency as amount, FX is meaningless.
        with pytest.raises(ValueError, match="currency"):
            Posting(
                id=uuid4(),
                account_id=uuid4(),
                amount=Money(Decimal("100.00"), "USD"),
                fx_rate=Decimal("1.0"),
                fx_amount=Money(Decimal("100.00"), "USD"),
            )
