"""Unit tests for CandidateMatch value object."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.money import Money
from tulip_core.reconciliation import CandidateMatch, MatchConfidence


def _kw(**overrides):
    base = {
        "statement_line_id": uuid4(),
        "ledger_transaction_id": uuid4(),
        "match_amount": Money(Decimal("-42.17"), "USD"),
        "confidence": MatchConfidence.HIGH,
        "fuzzy_score": 0.95,
    }
    base.update(overrides)
    return base


class TestConstruction:
    def test_minimal_valid(self):
        m = CandidateMatch(**_kw())
        assert m.confidence is MatchConfidence.HIGH
        assert m.fuzzy_score == 0.95

    def test_frozen(self):
        m = CandidateMatch(**_kw())
        with pytest.raises((AttributeError, TypeError)):
            m.confidence = MatchConfidence.LOW  # type: ignore[misc]


class TestValidation:
    def test_match_amount_must_be_money(self):
        with pytest.raises(TypeError, match="match_amount"):
            CandidateMatch(**_kw(match_amount=Decimal("-42.17")))

    def test_fuzzy_score_must_be_in_range(self):
        with pytest.raises(ValueError, match="fuzzy_score"):
            CandidateMatch(**_kw(fuzzy_score=-0.1))
        with pytest.raises(ValueError, match="fuzzy_score"):
            CandidateMatch(**_kw(fuzzy_score=1.01))

    def test_fuzzy_score_boundary_values_allowed(self):
        CandidateMatch(**_kw(fuzzy_score=0.0))
        CandidateMatch(**_kw(fuzzy_score=1.0))


class TestEquality:
    def test_eq_keyed_by_id_pair(self):
        sl = uuid4()
        tx = uuid4()
        a = CandidateMatch(**_kw(statement_line_id=sl, ledger_transaction_id=tx))
        # Same id pair, different confidence + fuzzy: still equal.
        b = CandidateMatch(
            **_kw(
                statement_line_id=sl,
                ledger_transaction_id=tx,
                confidence=MatchConfidence.LOW,
                fuzzy_score=0.05,
            )
        )
        assert a == b

    def test_different_line_id_not_equal(self):
        tx = uuid4()
        a = CandidateMatch(**_kw(ledger_transaction_id=tx))
        b = CandidateMatch(**_kw(ledger_transaction_id=tx))
        assert a != b

    def test_different_tx_id_not_equal(self):
        sl = uuid4()
        a = CandidateMatch(**_kw(statement_line_id=sl))
        b = CandidateMatch(**_kw(statement_line_id=sl))
        assert a != b

    def test_hashable_in_set(self):
        sl = uuid4()
        tx = uuid4()
        a = CandidateMatch(**_kw(statement_line_id=sl, ledger_transaction_id=tx))
        b = CandidateMatch(
            **_kw(
                statement_line_id=sl,
                ledger_transaction_id=tx,
                confidence=MatchConfidence.LOW,
                fuzzy_score=0.05,
            )
        )
        # Equal objects collapse in a set.
        assert {a, b} == {a}

    def test_not_equal_to_other_type(self):
        m = CandidateMatch(**_kw())
        assert m != "not a candidate match"
        assert m != (m.statement_line_id, m.ledger_transaction_id)
