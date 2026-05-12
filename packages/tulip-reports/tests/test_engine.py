"""Tests for the rendering engine + the toner-friendly base layout (P7.1)."""

from __future__ import annotations

from decimal import Decimal

from tulip_reports.engine import (
    _format_date,
    _format_money,
    _is_negative,
    get_renderer,
)


class TestMoneyFilter:
    def test_positive(self) -> None:
        assert _format_money(Decimal("1234.5")) == "1,234.50"

    def test_negative_uses_minus_sign(self) -> None:
        assert _format_money(Decimal("-100")) == "-100.00"

    def test_quantize_to_two_decimals(self) -> None:
        assert _format_money(Decimal("1.555")) == "1.56"  # banker's rounding

    def test_with_currency(self) -> None:
        assert _format_money(Decimal("10.5"), "USD") == "10.50 USD"

    def test_none_returns_empty_string(self) -> None:
        assert _format_money(None) == ""

    def test_string_input_coerced(self) -> None:
        assert _format_money("42.10") == "42.10"


class TestDateFilter:
    def test_iso_date(self) -> None:
        from datetime import date

        assert _format_date(date(2026, 5, 12)) == "2026-05-12"

    def test_datetime_uses_date_part(self) -> None:
        from datetime import datetime

        assert _format_date(datetime(2026, 5, 12, 9, 30)) == "2026-05-12"

    def test_none_returns_empty(self) -> None:
        assert _format_date(None) == ""


class TestNegativeTest:
    def test_positive_is_false(self) -> None:
        assert _is_negative(Decimal("5")) is False

    def test_zero_is_false(self) -> None:
        assert _is_negative(Decimal("0")) is False

    def test_negative_is_true(self) -> None:
        assert _is_negative(Decimal("-1.50")) is True

    def test_none_is_false(self) -> None:
        assert _is_negative(None) is False


class TestRenderer:
    def test_get_renderer_returns_singleton(self) -> None:
        a = get_renderer()
        b = get_renderer()
        assert a is b

    def test_base_template_renders(self) -> None:
        from datetime import date

        out = get_renderer().render("base.html", generated_at=date(2026, 5, 12))
        # Toner-friendly contract: white background, no full-page color fill.
        assert "background: #fff" in out
        # Sans-serif body font.
        assert "Inter" in out
        # Date filter applied to generated_at.
        assert "2026-05-12" in out

    def test_strict_undefined_catches_typos(self) -> None:
        import pytest
        from jinja2 import UndefinedError

        with pytest.raises(UndefinedError):
            get_renderer().render("base.html")  # generated_at missing
