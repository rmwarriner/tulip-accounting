"""Tests for tulip_importers.csv.parse."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tulip_core.reconciliation import ParsedStatementLine
from tulip_importers.csv import CsvParseError, CsvProfile, parse

FIXTURES = Path(__file__).parent / "fixtures" / "csv"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _chase_profile() -> CsvProfile:
    return CsvProfile(
        name="chase-checking",
        date_column="Posting Date",
        date_format="%m/%d/%Y",
        amount_column="Amount",
        description_column="Description",
        reference_column="Check or Slip #",
    )


def _amex_profile() -> CsvProfile:
    return CsvProfile(
        name="amex",
        date_column="Date",
        date_format="%Y-%m-%d",
        amount_column="Amount",
        amount_negative_means="credit",
        description_column="Description",
    )


def _metadata_profile() -> CsvProfile:
    return CsvProfile(
        name="bank-with-meta",
        date_column="Date",
        date_format="%Y-%m-%d",
        amount_column="Amount",
        description_column="Description",
        skip_header_rows=3,  # 2 metadata rows + 1 column header
    )


class TestParseHappy:
    def test_chase_checking_returns_four_lines(self):
        lines = parse(
            _read("chase_checking.csv"),
            profile=_chase_profile(),
            currency="USD",
        )
        assert len(lines) == 4
        assert all(isinstance(line, ParsedStatementLine) for line in lines)

    def test_chase_field_mapping(self):
        lines = parse(
            _read("chase_checking.csv"),
            profile=_chase_profile(),
            currency="USD",
        )
        amazon, _payroll, _lunch, check = lines

        assert amazon.line_number == 1
        assert amazon.posted_date == date(2026, 5, 12)
        assert amazon.amount.amount == Decimal("-42.17")
        assert amazon.amount.currency == "USD"
        # Quoted comma in Description survives.
        assert "PAYPAL" in amazon.description
        assert "AMAZON" in amazon.description
        assert "INC" in amazon.description
        # Reference column maps to N (Check #) — empty here.
        assert amazon.reference is None

        # Last row has the check number filled in.
        assert check.reference == "1234"
        assert check.amount.amount == Decimal("-200.00")

    def test_raw_dict_carries_all_columns(self):
        lines = parse(
            _read("chase_checking.csv"),
            profile=_chase_profile(),
            currency="USD",
        )
        first = lines[0]
        assert "Posting Date" in first.raw
        assert "Type" in first.raw
        assert "Balance" in first.raw

    def test_currency_kwarg_used(self):
        lines = parse(
            _read("chase_checking.csv"),
            profile=_chase_profile(),
            currency="EUR",
        )
        assert all(line.amount.currency == "EUR" for line in lines)


class TestAmountSignFlip:
    def test_credit_convention_flips_signs(self):
        # AMEX-style: positive = charge, negative = payment. After flip,
        # charges should be negative (money leaving account) per
        # ParsedStatementLine convention.
        lines = parse(
            _read("amex_credit.csv"),
            profile=_amex_profile(),
            currency="USD",
        )
        amazon, payment, starbucks = lines
        assert amazon.amount.amount == Decimal("-42.17")  # charge → negative
        assert payment.amount.amount == Decimal("1500.00")  # payment → positive
        assert starbucks.amount.amount == Decimal("-12.50")

    def test_debit_convention_default_no_flip(self):
        # Default profile: signs pass through.
        lines = parse(
            _read("chase_checking.csv"),
            profile=_chase_profile(),
            currency="USD",
        )
        # Chase fixture: payroll is +1500, debits are negative.
        amounts = sorted(line.amount.amount for line in lines)
        assert amounts == [
            Decimal("-200.00"),
            Decimal("-42.17"),
            Decimal("-12.50"),
            Decimal("1500.00"),
        ]


class TestSkipHeaderRows:
    def test_metadata_header_skipped(self):
        lines = parse(
            _read("metadata_header.csv"),
            profile=_metadata_profile(),
            currency="USD",
        )
        # 2 transactions; the blank row in the middle is skipped.
        assert len(lines) == 2
        assert lines[0].posted_date == date(2026, 5, 12)
        assert lines[1].posted_date == date(2026, 5, 13)


class TestEncoding:
    def test_utf8_bom_stripped_transparently(self):
        # Excel-style UTF-8 BOM at file start.
        body = (
            b"\xef\xbb\xbf"  # BOM
            b"Date,Description,Amount\n"
            b"2026-05-12,AMAZON,-10.00\n"
        )
        profile = CsvProfile(
            name="bom",
            date_column="Date",
            date_format="%Y-%m-%d",
            amount_column="Amount",
            description_column="Description",
        )
        lines = parse(body, profile=profile, currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("-10.00")


class TestQuotedFields:
    def test_embedded_newline_in_quoted_field(self):
        body = b'Date,Description,Amount\n2026-05-12,"line one\nline two",-10.00\n'
        profile = CsvProfile(
            name="multiline",
            date_column="Date",
            date_format="%Y-%m-%d",
            amount_column="Amount",
            description_column="Description",
        )
        lines = parse(body, profile=profile, currency="USD")
        assert len(lines) == 1
        assert "line one" in lines[0].description
        assert "line two" in lines[0].description


class TestErrors:
    def test_empty_bytes_raises(self):
        with pytest.raises(CsvParseError, match="empty"):
            parse(b"", profile=_chase_profile(), currency="USD")

    def test_missing_required_column_raises(self):
        # Profile expects "Posting Date" but the CSV has only "Date".
        body = b"Date,Description,Amount\n2026-05-12,X,-1.00\n"
        with pytest.raises(CsvParseError, match="Posting Date"):
            parse(body, profile=_chase_profile(), currency="USD")

    def test_bad_date_format_surfaces_row_number(self):
        body = (
            b"Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
            b"05/12/2026,Good,-10.00,D,0,\n"
            b"13/45/2026,Bad,-20.00,D,0,\n"
        )
        with pytest.raises(CsvParseError, match="row 2"):
            parse(body, profile=_chase_profile(), currency="USD")

    def test_bad_amount_surfaces_row_number(self):
        body = (
            b"Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
            b"05/12/2026,X,not-a-number,D,0,\n"
        )
        with pytest.raises(CsvParseError, match="row 1"):
            parse(body, profile=_chase_profile(), currency="USD")

    def test_empty_amount_surfaces_row_number(self):
        body = b"Posting Date,Description,Amount,Type,Balance,Check or Slip #\n05/12/2026,X,,D,0,\n"
        with pytest.raises(CsvParseError, match="row 1"):
            parse(body, profile=_chase_profile(), currency="USD")
