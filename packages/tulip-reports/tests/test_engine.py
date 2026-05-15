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

    def test_jpy_uses_zero_decimals(self) -> None:
        # Issue #213: currency-natural precision — JPY has 0 minor units.
        assert _format_money(Decimal("100.5"), "JPY") == "100 JPY"

    def test_bhd_uses_three_decimals(self) -> None:
        # Issue #213: BHD has 3 minor units.
        assert _format_money(Decimal("1.2"), "BHD") == "1.200 BHD"

    def test_unknown_currency_falls_back_to_two_decimals(self) -> None:
        # Two-decimal fallback for unknown ISO 4217 codes. Banker's rounding:
        # .555 → .56 (5 rounds to nearest even — .56 is even).
        assert _format_money(Decimal("12.555"), "XYZ") == "12.56 XYZ"

    def test_storage_precision_amount_quantizes_to_usd(self) -> None:
        # Issue #213: an 8-decimal storage amount renders as 2-decimal USD.
        assert _format_money(Decimal("12.20000000"), "USD") == "12.20 USD"


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

    def test_render_pdf_returns_pdf_bytes(self) -> None:
        """Engine's render_pdf produces real PDF bytes via weasyprint (P7.2)."""
        from datetime import date

        pdf = get_renderer().render_pdf("base.html", generated_at=date(2026, 5, 12))
        assert isinstance(pdf, bytes)
        assert pdf.startswith(b"%PDF-")
