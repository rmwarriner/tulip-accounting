"""Unit tests for Currency value object."""

from __future__ import annotations

import pytest

from tulip_core.currency import Currency


class TestCurrencyConstruction:
    def test_constructs_from_valid_iso_4217_code(self):
        c = Currency.from_code("USD")
        assert c.code == "USD"

    @pytest.mark.parametrize("bad", ["USDX", "usd", "X", "", "12A"])
    def test_invalid_codes_raise(self, bad: str):
        with pytest.raises(ValueError, match="ISO 4217"):
            Currency.from_code(bad)

    def test_unknown_three_letter_code_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            Currency.from_code("ZZZ")


class TestCurrencyMinorUnits:
    @pytest.mark.parametrize(
        ("code", "minor_units"),
        [("USD", 2), ("EUR", 2), ("JPY", 0), ("BHD", 3)],
    )
    def test_known_minor_units(self, code: str, minor_units: int):
        assert Currency.from_code(code).minor_units == minor_units


class TestCurrencyEqualityAndInterning:
    def test_same_code_is_equal(self):
        assert Currency.from_code("USD") == Currency.from_code("USD")

    def test_different_codes_not_equal(self):
        assert Currency.from_code("USD") != Currency.from_code("EUR")

    def test_from_code_returns_canonical_instance(self):
        # Caching/interning: repeated lookups return the same object.
        a = Currency.from_code("USD")
        b = Currency.from_code("USD")
        assert a is b
