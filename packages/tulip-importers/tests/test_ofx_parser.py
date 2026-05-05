"""Unit tests for tulip_importers.ofx.parse."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from tulip_core.reconciliation import ParsedStatementLine
from tulip_importers.ofx import OfxParseError, parse

FIXTURES = Path(__file__).parent / "fixtures" / "ofx"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParseHappy:
    def test_ofx2_minimal_returns_two_lines(self):
        lines = parse(_read("minimal_ofx2.ofx"))
        assert len(lines) == 2
        assert all(isinstance(line, ParsedStatementLine) for line in lines)

    def test_ofx2_line_field_mapping(self):
        # Order is source-file order; line_number is 1-based.
        lines = parse(_read("minimal_ofx2.ofx"))
        amazon, paycheck = lines

        assert amazon.line_number == 1
        assert amazon.posted_date == date(2026, 5, 12)
        assert amazon.amount.amount == Decimal("-42.17")
        assert amazon.amount.currency == "USD"
        assert "PAYPAL" in amazon.description
        assert "AMAZON KINDLE" in amazon.description
        assert amazon.fitid == "FITID-AMAZON-001"
        assert amazon.reference == "FITID-AMAZON-001"
        assert amazon.raw["FITID"] == "FITID-AMAZON-001"

        assert paycheck.line_number == 2
        assert paycheck.amount.amount == Decimal("1500.00")
        assert paycheck.fitid == "FITID-PAYCHECK-001"
        # Memo absent on this line; description carries NAME only.
        assert "PAYROLL" in paycheck.description

    def test_ofx2_raw_is_immutable(self):
        line = parse(_read("minimal_ofx2.ofx"))[0]
        assert isinstance(line.raw, MappingProxyType)
        with pytest.raises(TypeError):
            line.raw["evil"] = "x"  # type: ignore[index]

    def test_ofx1_sgml_returns_one_line(self):
        # OFX 1.x SGML is what most US banks still emit. ofxtools handles
        # both 1.x and 2.x through OFXTree; this fixture verifies the SGML
        # path doesn't fall through cracks at our seam.
        lines = parse(_read("minimal_ofx1.sgml"))
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("-12.50")
        assert lines[0].fitid == "FITID-LUNCH-001"
        assert "DELI" in lines[0].description

    def test_empty_ofx_returns_empty_list(self):
        # Structurally-valid OFX with no STMTTRNs returns []; "valid OFX,
        # no transactions" must be distinguishable from "not OFX".
        lines = parse(_read("empty.ofx"))
        assert lines == []


class TestParseErrors:
    def test_malformed_bytes_raises_typed_error(self):
        with pytest.raises(OfxParseError) as exc_info:
            parse(b"<not-ofx>random garbage</not-ofx>")
        # Cause is preserved for debugging.
        assert exc_info.value.__cause__ is not None

    def test_zero_bytes_raises(self):
        # An empty payload is not valid OFX (vs. valid-but-empty OFX, which
        # is empty.ofx and returns []). Distinguishing the two matters.
        with pytest.raises(OfxParseError):
            parse(b"")

    def test_garbage_text_raises(self):
        with pytest.raises(OfxParseError):
            parse(b"this is not ofx at all")
