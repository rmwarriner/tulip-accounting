"""Unit tests for the CLI display-precision helper (issue #213)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_cli._money_format import format_amount


class TestFormatAmount:
    def test_strips_excess_trailing_zeros_for_usd(self) -> None:
        # The motivating case: QIF imports persist amounts at full storage
        # precision (8 decimals); the display layer must shorten to 2 for USD.
        assert format_amount("12.20000000", "USD") == "12.20"

    def test_pads_short_decimals_for_usd(self) -> None:
        assert format_amount("12", "USD") == "12.00"
        assert format_amount("12.5", "USD") == "12.50"

    def test_jpy_uses_zero_decimals(self) -> None:
        assert format_amount("100.50000000", "JPY") == "100"
        # Banker's rounding: .5 rounds to nearest even, so 100.5 → 100.
        assert format_amount("100.5", "JPY") == "100"
        assert format_amount("101.5", "JPY") == "102"

    def test_bhd_uses_three_decimals(self) -> None:
        assert format_amount("1.20000000", "BHD") == "1.200"

    def test_unknown_currency_falls_back_to_two_decimals(self) -> None:
        assert format_amount("12.20000000", "XYZ") == "12.20"

    def test_empty_currency_falls_back_to_two_decimals(self) -> None:
        assert format_amount("12.20000000", "") == "12.20"
        assert format_amount("12.20000000", None) == "12.20"

    def test_decimal_input(self) -> None:
        assert format_amount(Decimal("12.2"), "USD") == "12.20"

    def test_none_amount_renders_empty_string(self) -> None:
        assert format_amount(None, "USD") == ""

    def test_negative_amount(self) -> None:
        assert format_amount("-42.17000000", "USD") == "-42.17"

    def test_non_numeric_passthrough(self) -> None:
        # The renderer must not crash on garbage; falls back to str().
        assert format_amount("not-a-number", "USD") == "not-a-number"

    def test_currency_is_case_insensitive(self) -> None:
        assert format_amount("100.5", "jpy") == "100"


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("12.20000000", "USD", "12.20"),
        ("0.00000000", "USD", "0.00"),
        ("1234.56789", "EUR", "1234.57"),  # banker's rounding to 2dp
        ("100", "JPY", "100"),
    ],
)
def test_parametrized(amount: str, currency: str, expected: str) -> None:
    assert format_amount(amount, currency) == expected
