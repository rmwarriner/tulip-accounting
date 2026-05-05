"""Unit tests for ParsedStatementLine + StatementLine value objects."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

import pytest

from tulip_core.money import Money
from tulip_core.reconciliation import ParsedStatementLine, StatementLine


def _valid_parsed_kwargs(**overrides):
    """Return a kwargs dict for a valid ParsedStatementLine; allow per-test overrides."""
    base = dict(
        line_number=1,
        posted_date=date(2026, 5, 12),
        amount=Money(Decimal("-42.17"), "USD"),
        description="PAYPAL *AMAZON",
        counterparty=None,
        reference="FITID-X",
        raw={"FITID": "FITID-X"},
    )
    base.update(overrides)
    return base


def _valid_persisted_kwargs(**overrides):
    base = _valid_parsed_kwargs()
    base["id"] = uuid4()
    base["import_batch_id"] = uuid4()
    base.update(overrides)
    return base


class TestParsedStatementLineConstruction:
    def test_minimal_valid_line(self):
        line = ParsedStatementLine(**_valid_parsed_kwargs())
        assert line.line_number == 1
        assert line.amount.amount == Decimal("-42.17")
        assert line.amount.currency == "USD"
        assert line.description == "PAYPAL *AMAZON"

    def test_frozen(self):
        line = ParsedStatementLine(**_valid_parsed_kwargs())
        with pytest.raises((AttributeError, TypeError)):
            line.line_number = 2  # type: ignore[misc]

    def test_raw_is_immutable_view(self):
        # The parser passes a dict; the value object exposes a read-only view.
        # External callers can't mutate the row's raw dict after construction.
        line = ParsedStatementLine(**_valid_parsed_kwargs())
        assert isinstance(line.raw, MappingProxyType)
        with pytest.raises(TypeError):
            line.raw["FITID"] = "tampered"  # type: ignore[index]

    def test_amount_must_be_money(self):
        with pytest.raises(TypeError, match="amount"):
            ParsedStatementLine(**_valid_parsed_kwargs(amount=Decimal("10.00")))

    def test_description_must_be_non_empty(self):
        with pytest.raises(ValueError, match="description"):
            ParsedStatementLine(**_valid_parsed_kwargs(description=""))

    def test_description_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="description"):
            ParsedStatementLine(**_valid_parsed_kwargs(description="   "))

    def test_line_number_must_be_positive(self):
        with pytest.raises(ValueError, match="line_number"):
            ParsedStatementLine(**_valid_parsed_kwargs(line_number=0))
        with pytest.raises(ValueError, match="line_number"):
            ParsedStatementLine(**_valid_parsed_kwargs(line_number=-1))

    def test_optional_fields_default_to_none(self):
        # raw is required; everything else can be omitted.
        line = ParsedStatementLine(
            line_number=1,
            posted_date=date(2026, 5, 12),
            amount=Money(Decimal("-1.00"), "USD"),
            description="x",
            raw={},
        )
        assert line.counterparty is None
        assert line.reference is None
        assert line.fitid is None

    def test_fitid_passes_through_when_set(self):
        line = ParsedStatementLine(**_valid_parsed_kwargs(fitid="FITID-Y"))
        assert line.fitid == "FITID-Y"


class TestStatementLineConstruction:
    def test_minimal_valid_persisted_line(self):
        kwargs = _valid_persisted_kwargs()
        line = StatementLine(**kwargs)
        assert line.id == kwargs["id"]
        assert line.import_batch_id == kwargs["import_batch_id"]
        assert line.line_number == 1

    def test_frozen(self):
        line = StatementLine(**_valid_persisted_kwargs())
        with pytest.raises((AttributeError, TypeError)):
            line.line_number = 2  # type: ignore[misc]

    def test_inherits_validation_from_parsed(self):
        # Same rules as ParsedStatementLine — empty description still rejected.
        with pytest.raises(ValueError, match="description"):
            StatementLine(**_valid_persisted_kwargs(description=""))

    def test_equality_by_id(self):
        kwargs = _valid_persisted_kwargs()
        a = StatementLine(**kwargs)
        b = StatementLine(**kwargs)
        assert a == b
        # Same id, different description → still equal (id-based).
        c = StatementLine(**{**kwargs, "description": "different"})
        assert a == c
        # Different id → not equal.
        d = StatementLine(**{**kwargs, "id": uuid4()})
        assert a != d


class TestParsedToStatementLine:
    def test_promote_to_persisted(self):
        # The API handler will materialize ParsedStatementLine → StatementLine
        # by adding id + import_batch_id. We surface a small helper so callers
        # don't have to reach for replace() incantations.
        parsed = ParsedStatementLine(**_valid_parsed_kwargs())
        persisted_id = uuid4()
        batch_id = uuid4()
        line = parsed.with_persistence_ids(id=persisted_id, import_batch_id=batch_id)
        assert isinstance(line, StatementLine)
        assert line.id == persisted_id
        assert line.import_batch_id == batch_id
        # All other fields preserved.
        assert line.line_number == parsed.line_number
        assert line.amount == parsed.amount
        assert line.description == parsed.description
        assert line.fitid == parsed.fitid
        assert dict(line.raw) == dict(parsed.raw)
