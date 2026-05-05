"""Unit tests for tulip_importers.qif.parse."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tulip_core.reconciliation import ParsedStatementLine
from tulip_importers.qif import QifParseError, parse

FIXTURES = Path(__file__).parent / "fixtures" / "qif"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParseHappy:
    def test_minimal_returns_three_lines(self):
        lines = parse(_read("minimal.qif"), currency="USD")
        assert len(lines) == 3
        assert all(isinstance(line, ParsedStatementLine) for line in lines)

    def test_field_mapping(self):
        # Source-file order; line_number is 1-based.
        lines = parse(_read("minimal.qif"), currency="USD")
        amazon, paycheck, lunch = lines

        assert amazon.line_number == 1
        assert amazon.posted_date == date(2026, 5, 12)
        assert amazon.amount.amount == Decimal("-42.17")
        assert amazon.amount.currency == "USD"
        # Description = payee + " " + memo (matches OFX convention).
        assert "PAYPAL" in amazon.description
        assert "AMAZON KINDLE" in amazon.description
        # N field → reference.
        assert amazon.reference == "CHECK1234"
        # raw carries the type header + the original field values.
        assert amazon.raw.get("TYPE") == "Bank"

        assert paycheck.line_number == 2
        assert paycheck.amount.amount == Decimal("1500.00")
        # Memo absent → description has only payee.
        assert "PAYROLL" in paycheck.description
        assert paycheck.reference is None

        # ISO date support.
        assert lunch.posted_date == date(2026, 5, 20)

    def test_two_digit_year_rolls_to_2000s(self):
        # MM/DD/YY → 20YY (no banks emitting 19xx files in 2026+).
        lines = parse(_read("two_digit_year.qif"), currency="USD")
        assert lines[0].posted_date == date(2026, 5, 12)

    def test_currency_arg_is_used(self):
        # QIF carries no currency; caller (API) supplies the account's.
        lines = parse(_read("minimal.qif"), currency="EUR")
        assert lines[0].amount.currency == "EUR"

    def test_empty_qif_returns_empty_list(self):
        # Header-only file (no transactions) returns [].
        lines = parse(_read("empty.qif"), currency="USD")
        assert lines == []

    def test_no_type_header_still_parses(self):
        # !Type:Bank header is conventional but optional; some banks omit it.
        lines = parse(_read("no_header.qif"), currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("-42.17")


class TestParseErrors:
    def test_empty_bytes_raises(self):
        with pytest.raises(QifParseError):
            parse(b"", currency="USD")

    def test_garbage_bytes_raises(self):
        # No `^` separator + no valid field codes = not QIF.
        with pytest.raises(QifParseError):
            parse(b"this is not a qif file at all", currency="USD")

    def test_record_missing_amount_raises(self):
        # T (amount) is mandatory per ADR §Q8 — without it the line can't
        # produce a Money value object. Surface line context.
        bad = b"!Type:Bank\nD5/12/2026\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="amount"):
            parse(bad, currency="USD")

    def test_record_missing_date_raises(self):
        bad = b"!Type:Bank\nT-12.50\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="date"):
            parse(bad, currency="USD")

    def test_unparseable_amount_raises(self):
        bad = b"!Type:Bank\nD5/12/2026\nTnot-a-number\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="amount"):
            parse(bad, currency="USD")

    def test_unparseable_date_raises(self):
        bad = b"!Type:Bank\nDgarbage\nT-12.50\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="date"):
            parse(bad, currency="USD")
