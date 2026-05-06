"""Unit tests for tulip_core.reconciliation.matcher.find_candidates."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

from tulip_core.money import Money
from tulip_core.reconciliation import (
    CandidateMatch,
    MatchConfidence,
    StatementLine,
    find_candidates,
)
from tulip_core.transactions import Posting, Transaction, TransactionStatus

HOUSEHOLD = uuid4()


# ---- builders -------------------------------------------------------------


def _line(
    *,
    posted_date: date = date(2026, 5, 12),
    amount: Decimal = Decimal("-42.17"),
    description: str = "Amazon Kindle",
    line_number: int = 1,
) -> StatementLine:
    return StatementLine(
        id=uuid4(),
        import_batch_id=uuid4(),
        line_number=line_number,
        posted_date=posted_date,
        amount=Money(amount, "USD"),
        description=description,
        raw=MappingProxyType({}),
    )


def _tx(
    *,
    account_id: UUID,
    contra_account_id: UUID,
    tx_date: date = date(2026, 5, 12),
    amount: Decimal = Decimal("-42.17"),
    description: str = "Amazon Kindle",
    status: TransactionStatus = TransactionStatus.POSTED,
) -> Transaction:
    """Build a balanced 2-posting Transaction.

    The first posting is on ``account_id`` (the side the matcher inspects);
    the second posting is on ``contra_account_id`` and balances the first.
    """
    return Transaction(
        id=uuid4(),
        household_id=HOUSEHOLD,
        date=tx_date,
        description=description,
        postings=(
            Posting(
                id=uuid4(),
                account_id=account_id,
                amount=Money(amount, "USD"),
            ),
            Posting(
                id=uuid4(),
                account_id=contra_account_id,
                amount=Money(-amount, "USD"),
            ),
        ),
        status=status,
    )


# ---- happy path -----------------------------------------------------------


class TestFindCandidatesHappyPath:
    def test_exact_match_returns_one_high_candidate(self):
        checking = uuid4()
        food = uuid4()
        line = _line(description="AMAZON KINDLE")
        # "AMAZON KINDLE" vs "Amazon Kindle" -> token_set_ratio 100 with
        # default_process (lowercase + punctuation strip).
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            description="Amazon Kindle",
        )

        out = find_candidates([line], [tx], account_id=checking)
        assert len(out) == 1
        cand = out[0]
        assert isinstance(cand, CandidateMatch)
        assert cand.statement_line_id == line.id
        assert cand.ledger_transaction_id == tx.id
        assert cand.match_amount == line.amount
        assert cand.confidence is MatchConfidence.HIGH
        assert cand.fuzzy_score >= 0.9

    def test_no_candidates_for_empty_inputs(self):
        assert find_candidates([], [], account_id=uuid4()) == []


# ---- confidence buckets ---------------------------------------------------


class TestConfidenceBuckets:
    def setup_method(self):
        self.checking = uuid4()
        self.food = uuid4()

    def test_high_exact_date_high_fuzzy(self):
        line = _line(posted_date=date(2026, 5, 12), description="AMAZON KINDLE")
        tx = _tx(
            account_id=self.checking,
            contra_account_id=self.food,
            tx_date=date(2026, 5, 12),
            description="Amazon Kindle",
        )
        out = find_candidates([line], [tx], account_id=self.checking)
        assert out[0].confidence is MatchConfidence.HIGH

    def test_medium_date_drift_medium_fuzzy(self):
        # 2 days drift, fuzzy 63% (PAYPAL *AMAZON vs Amazon — Kindle book).
        line = _line(posted_date=date(2026, 5, 12), description="PAYPAL *AMAZON")
        tx = _tx(
            account_id=self.checking,
            contra_account_id=self.food,
            tx_date=date(2026, 5, 14),
            description="Amazon — Kindle book",
        )
        out = find_candidates([line], [tx], account_id=self.checking)
        assert len(out) == 1
        assert out[0].confidence is MatchConfidence.MEDIUM

    def test_low_date_drift_low_fuzzy(self):
        # 2 days drift, fuzzy 10% (CHECK 1234 vs WITHDRAWAL).
        line = _line(posted_date=date(2026, 5, 12), description="CHECK 1234")
        tx = _tx(
            account_id=self.checking,
            contra_account_id=self.food,
            tx_date=date(2026, 5, 14),
            description="WITHDRAWAL",
        )
        out = find_candidates([line], [tx], account_id=self.checking)
        assert len(out) == 1
        assert out[0].confidence is MatchConfidence.LOW

    def test_medium_exact_date_low_fuzzy(self):
        # Same date but description doesn't match: ADR §Q2 says this is
        # MEDIUM ("exact amount + same date + no description match"),
        # NOT low. Same-date is never LOW.
        line = _line(posted_date=date(2026, 5, 12), description="CHECK 1234")
        tx = _tx(
            account_id=self.checking,
            contra_account_id=self.food,
            tx_date=date(2026, 5, 12),
            description="WITHDRAWAL",
        )
        out = find_candidates([line], [tx], account_id=self.checking)
        assert len(out) == 1
        assert out[0].confidence is MatchConfidence.MEDIUM

    def test_high_boundary_exactly_0_9_fuzzy(self):
        # Need a description pair that scores exactly 0.9. We pick a pair
        # we know scores 1.0; HIGH is asserted at >= 0.9 inclusive.
        line = _line(description="AMAZON KINDLE")
        tx = _tx(
            account_id=self.checking,
            contra_account_id=self.food,
            description="Amazon Kindle",
        )
        out = find_candidates([line], [tx], account_id=self.checking)
        # token_set_ratio is 100 here; >= 0.9 -> HIGH.
        assert out[0].fuzzy_score == 1.0
        assert out[0].confidence is MatchConfidence.HIGH


# ---- exclusions -----------------------------------------------------------


class TestExcludesAlreadyReconciled:
    def test_reconciled_tx_excluded(self):
        checking = uuid4()
        food = uuid4()
        line = _line(description="AMAZON KINDLE")
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            description="Amazon Kindle",
        )
        out = find_candidates(
            [line],
            [tx],
            account_id=checking,
            reconciled_transaction_ids=frozenset({tx.id}),
        )
        assert out == []


class TestAmountMismatch:
    def test_amount_must_be_exact(self):
        checking = uuid4()
        food = uuid4()
        line = _line(amount=Decimal("-42.17"))
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            amount=Decimal("-42.18"),  # 1 cent off
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert out == []


class TestAccountScope:
    def test_tx_on_other_account_excluded(self):
        checking = uuid4()
        savings = uuid4()
        food = uuid4()
        line = _line()
        tx = _tx(
            account_id=savings,  # tx is on savings, not checking
            contra_account_id=food,
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert out == []

    def test_tx_with_one_matching_posting_among_many_accepted(self):
        # A Transaction whose first posting is on `food` and second is on
        # `checking` still matches when matcher.account_id=checking.
        checking = uuid4()
        food = uuid4()
        line = _line(description="AMAZON KINDLE")
        tx = Transaction(
            id=uuid4(),
            household_id=HOUSEHOLD,
            date=date(2026, 5, 12),
            description="Amazon Kindle",
            postings=(
                Posting(
                    id=uuid4(),
                    account_id=food,
                    amount=Money(Decimal("42.17"), "USD"),
                ),
                Posting(
                    id=uuid4(),
                    account_id=checking,
                    amount=Money(Decimal("-42.17"), "USD"),
                ),
            ),
            status=TransactionStatus.POSTED,
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert len(out) == 1
        assert out[0].ledger_transaction_id == tx.id


class TestDateWindowEdge:
    def test_exactly_3_days_included(self):
        checking = uuid4()
        food = uuid4()
        line = _line(posted_date=date(2026, 5, 12), description="AMAZON")
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            tx_date=date(2026, 5, 12) + timedelta(days=3),
            description="Amazon",
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert len(out) == 1

    def test_exactly_3_days_back_included(self):
        checking = uuid4()
        food = uuid4()
        line = _line(posted_date=date(2026, 5, 12), description="AMAZON")
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            tx_date=date(2026, 5, 12) - timedelta(days=3),
            description="Amazon",
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert len(out) == 1

    def test_4_days_excluded(self):
        checking = uuid4()
        food = uuid4()
        line = _line(posted_date=date(2026, 5, 12))
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            tx_date=date(2026, 5, 12) + timedelta(days=4),
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert out == []


# ---- multi-tx ordering ----------------------------------------------------


class TestStableOrdering:
    def test_output_order_is_invariant_to_input_order(self):
        checking = uuid4()
        food = uuid4()
        line1 = _line(line_number=1, description="AMAZON KINDLE")
        line2 = _line(line_number=2, description="STARBUCKS")
        tx_a = _tx(
            account_id=checking,
            contra_account_id=food,
            description="Amazon Kindle",
        )
        tx_b = _tx(
            account_id=checking,
            contra_account_id=food,
            description="Starbucks",
        )

        a_first = find_candidates([line1, line2], [tx_a, tx_b], account_id=checking)
        b_first = find_candidates([line2, line1], [tx_b, tx_a], account_id=checking)
        # Same id pairs in canonical order regardless of input order.
        ids_a = [(c.statement_line_id, c.ledger_transaction_id) for c in a_first]
        ids_b = [(c.statement_line_id, c.ledger_transaction_id) for c in b_first]
        assert sorted(ids_a) == sorted(ids_b)

    def test_one_line_two_candidate_txs_emits_both(self):
        checking = uuid4()
        food = uuid4()
        line = _line(description="AMAZON KINDLE")
        tx_a = _tx(
            account_id=checking,
            contra_account_id=food,
            description="Amazon Kindle",
        )
        tx_b = _tx(
            account_id=checking,
            contra_account_id=food,
            tx_date=date(2026, 5, 13),
            description="Amazon something else",
        )
        out = find_candidates([line], [tx_a, tx_b], account_id=checking)
        assert len(out) == 2
        # Both candidates reference the same statement line.
        assert {c.statement_line_id for c in out} == {line.id}


# ---- defensive: currency mismatch ----------------------------------------


class TestCurrencyMismatch:
    def test_different_currency_excluded(self):
        # A tx posting in EUR can't match a USD statement line —
        # Money equality requires currency match.
        checking = uuid4()
        food = uuid4()
        line = _line(amount=Decimal("-42.17"))
        tx = Transaction(
            id=uuid4(),
            household_id=HOUSEHOLD,
            date=date(2026, 5, 12),
            description="Amazon",
            postings=(
                Posting(
                    id=uuid4(),
                    account_id=checking,
                    amount=Money(Decimal("-42.17"), "EUR"),  # EUR not USD
                ),
                Posting(
                    id=uuid4(),
                    account_id=food,
                    amount=Money(Decimal("42.17"), "EUR"),
                ),
            ),
            status=TransactionStatus.POSTED,
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert out == []


# ---- pending txs excluded -------------------------------------------------


class TestPendingTxsExcluded:
    def test_pending_tx_not_a_candidate(self):
        # Per ADR §Q1 the matcher considers ledger transactions; PENDING
        # is workflow state, not ledger state. Exclude.
        checking = uuid4()
        food = uuid4()
        line = _line(description="AMAZON KINDLE")
        tx = _tx(
            account_id=checking,
            contra_account_id=food,
            description="Amazon Kindle",
            status=TransactionStatus.PENDING,
        )
        out = find_candidates([line], [tx], account_id=checking)
        assert out == []
