"""Hypothesis property tests for the matcher (P5.3 / ADR-0004 §Q2).

The bucket-classification function ``_classify_confidence`` is a small
piece of branch logic but the boundaries are easy to flip on a refactor.
Property tests assert the boundary invariants directly without locking
in particular description strings.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

from hypothesis import given
from hypothesis import strategies as st

from tulip_core.money import Money
from tulip_core.reconciliation import (
    MatchConfidence,
    StatementLine,
    find_candidates,
)
from tulip_core.reconciliation.matcher import (
    FUZZY_HIGH_THRESHOLD,
    FUZZY_MEDIUM_THRESHOLD,
    MATCH_DATE_WINDOW,
    _classify_confidence,
)
from tulip_core.transactions import Posting, Transaction, TransactionStatus

HOUSEHOLD = uuid4()


# ---- _classify_confidence boundary properties -----------------------------


@given(
    delta=st.integers(min_value=0, max_value=MATCH_DATE_WINDOW.days),
    fuzzy=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_classification_obeys_adr_boundaries(delta: int, fuzzy: float) -> None:
    """The §Q2 rules implied by the constants — exhaustively across the window."""
    confidence = _classify_confidence(delta, fuzzy)

    if delta == 0:
        # Same date: HIGH iff fuzzy >= threshold; otherwise MEDIUM. Never LOW.
        if fuzzy >= FUZZY_HIGH_THRESHOLD:
            assert confidence is MatchConfidence.HIGH
        else:
            assert confidence is MatchConfidence.MEDIUM
    else:
        # Date drift within window: MEDIUM iff fuzzy >= medium threshold,
        # else LOW. Never HIGH (HIGH requires same date).
        if fuzzy >= FUZZY_MEDIUM_THRESHOLD:
            assert confidence is MatchConfidence.MEDIUM
        else:
            assert confidence is MatchConfidence.LOW


@given(
    delta=st.integers(min_value=0, max_value=MATCH_DATE_WINDOW.days),
    fuzzy=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_classification_never_returns_low_on_same_date(delta: int, fuzzy: float) -> None:
    """ADR §Q2: same-date is never LOW even with no description match."""
    confidence = _classify_confidence(delta, fuzzy)
    if delta == 0:
        assert confidence is not MatchConfidence.LOW


@given(
    delta=st.integers(min_value=1, max_value=MATCH_DATE_WINDOW.days),
    fuzzy=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_classification_never_returns_high_on_drift(delta: int, fuzzy: float) -> None:
    """ADR §Q2: HIGH requires same date; date drift caps at MEDIUM."""
    confidence = _classify_confidence(delta, fuzzy)
    assert confidence is not MatchConfidence.HIGH


# ---- find_candidates output invariants ------------------------------------


def _line_at(line_id, posted_date: date, description: str = "x x x") -> StatementLine:
    return StatementLine(
        id=line_id,
        import_batch_id=uuid4(),
        line_number=1,
        posted_date=posted_date,
        amount=Money(Decimal("-10.00"), "USD"),
        description=description,
        raw=MappingProxyType({}),
    )


def _tx_at(tx_id, account_id, contra_id, tx_date: date, description: str = "x x x") -> Transaction:
    return Transaction(
        id=tx_id,
        household_id=HOUSEHOLD,
        date=tx_date,
        description=description,
        postings=(
            Posting(
                id=uuid4(),
                account_id=account_id,
                amount=Money(Decimal("-10.00"), "USD"),
            ),
            Posting(
                id=uuid4(),
                account_id=contra_id,
                amount=Money(Decimal("10.00"), "USD"),
            ),
        ),
        status=TransactionStatus.POSTED,
    )


@given(delta=st.integers(min_value=4, max_value=365))
def test_outside_window_emits_no_candidates(delta: int) -> None:
    """Date drift > MATCH_DATE_WINDOW always excludes the candidate."""
    checking = uuid4()
    food = uuid4()
    line_id = uuid4()
    tx_id = uuid4()
    base = date(2026, 5, 12)
    line = _line_at(line_id, base)
    tx = _tx_at(tx_id, checking, food, base + timedelta(days=delta))
    assert find_candidates([line], [tx], account_id=checking) == []


@given(
    delta=st.integers(min_value=0, max_value=MATCH_DATE_WINDOW.days),
)
def test_within_window_emits_at_most_one_candidate_per_pair(delta: int) -> None:
    """Within window, exactly one candidate per (line, tx) — no duplicates."""
    checking = uuid4()
    food = uuid4()
    line_id = uuid4()
    tx_id = uuid4()
    base = date(2026, 5, 12)
    line = _line_at(line_id, base, "Amazon")
    tx = _tx_at(tx_id, checking, food, base + timedelta(days=delta), "Amazon")
    out = find_candidates([line], [tx], account_id=checking)
    assert len(out) == 1
    # And the id-pair is unique.
    assert (out[0].statement_line_id, out[0].ledger_transaction_id) == (
        line_id,
        tx_id,
    )
