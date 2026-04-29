"""Unit tests for the accounting engine — post_transaction and friends."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.accounting import (
    ClosedPeriodError,
    UnbalancedTransactionError,
    balance_with_fx_postings,
    post_transaction,
)
from tulip_core.money import Money
from tulip_core.periods import Period, PeriodStatus
from tulip_core.transactions import Posting, Transaction, TransactionStatus

HOUSEHOLD = uuid4()


def _open_period() -> Period:
    return Period(
        id=uuid4(),
        household_id=HOUSEHOLD,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=PeriodStatus.OPEN,
    )


def _closed_period() -> Period:
    return Period(
        id=uuid4(),
        household_id=HOUSEHOLD,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        status=PeriodStatus.SOFT_CLOSED,
    )


def _balanced_pending_tx(when: date) -> Transaction:
    return Transaction(
        id=uuid4(),
        household_id=HOUSEHOLD,
        date=when,
        description="Coffee",
        postings=(
            Posting(
                id=uuid4(),
                account_id=uuid4(),
                amount=Money(Decimal("10.00"), "USD"),
            ),
            Posting(
                id=uuid4(),
                account_id=uuid4(),
                amount=Money(Decimal("-10.00"), "USD"),
            ),
        ),
        status=TransactionStatus.PENDING,
    )


class TestPostTransaction:
    def test_pending_balanced_becomes_posted(self):
        tx = _balanced_pending_tx(date(2026, 6, 1))
        posted = post_transaction(tx, periods=[_open_period()])
        assert posted.status is TransactionStatus.POSTED
        assert posted.id == tx.id
        # Postings unchanged.
        assert posted.postings == tx.postings

    def test_already_posted_passes_through(self):
        tx = Transaction(
            id=uuid4(),
            household_id=HOUSEHOLD,
            date=date(2026, 6, 1),
            description="Coffee",
            postings=(
                Posting(
                    id=uuid4(),
                    account_id=uuid4(),
                    amount=Money(Decimal("10.00"), "USD"),
                ),
                Posting(
                    id=uuid4(),
                    account_id=uuid4(),
                    amount=Money(Decimal("-10.00"), "USD"),
                ),
            ),
            status=TransactionStatus.POSTED,
        )
        posted = post_transaction(tx, periods=[_open_period()])
        assert posted is tx  # identity preserved

    def test_unbalanced_pending_raises(self):
        unbalanced = Transaction(
            id=uuid4(),
            household_id=HOUSEHOLD,
            date=date(2026, 6, 1),
            description="Bad",
            postings=(
                Posting(
                    id=uuid4(),
                    account_id=uuid4(),
                    amount=Money(Decimal("10.00"), "USD"),
                ),
                Posting(
                    id=uuid4(),
                    account_id=uuid4(),
                    amount=Money(Decimal("-9.00"), "USD"),
                ),
            ),
            status=TransactionStatus.PENDING,
        )
        with pytest.raises(UnbalancedTransactionError):
            post_transaction(unbalanced, periods=[_open_period()])

    def test_no_period_for_date_raises(self):
        # No period covers date 2024-06-01.
        tx = _balanced_pending_tx(date(2024, 6, 1))
        with pytest.raises(ClosedPeriodError, match="no period"):
            post_transaction(tx, periods=[_open_period()])

    def test_closed_period_raises_without_override(self):
        tx = _balanced_pending_tx(date(2025, 6, 1))
        with pytest.raises(ClosedPeriodError):
            post_transaction(tx, periods=[_open_period(), _closed_period()])

    def test_closed_period_allowed_with_override(self):
        tx = _balanced_pending_tx(date(2025, 6, 1))
        posted = post_transaction(
            tx,
            periods=[_open_period(), _closed_period()],
            allow_closed_period_override=True,
        )
        assert posted.status is TransactionStatus.POSTED


class TestBalanceWithFxPostings:
    def test_balanced_input_returns_unchanged(self):
        tx = _balanced_pending_tx(date(2026, 6, 1))
        fx_acct = uuid4()
        result = balance_with_fx_postings(tx, fx_gain_loss_account_id=fx_acct, base_currency="USD")
        assert result is tx

    def test_eur_imbalance_balanced_with_fx_postings_to_base(self):
        # Two USD postings net to -10 USD (cash leaving), two EUR postings
        # net to +10 EUR (the "cost" leg). Engine adds two postings:
        #   +10 USD to FX g/l (offsets USD imbalance)
        #   -10 EUR to FX g/l (offsets EUR imbalance)
        # so the resulting transaction balances per currency.
        fx_acct = uuid4()
        cash_usd = uuid4()
        expense_eur = uuid4()
        unbal = Transaction(
            id=uuid4(),
            household_id=HOUSEHOLD,
            date=date(2026, 6, 1),
            description="Pay EUR vendor with USD",
            postings=(
                Posting(
                    id=uuid4(),
                    account_id=cash_usd,
                    amount=Money(Decimal("-10.00"), "USD"),
                ),
                Posting(
                    id=uuid4(),
                    account_id=expense_eur,
                    amount=Money(Decimal("10.00"), "EUR"),
                ),
            ),
            status=TransactionStatus.PENDING,
        )
        balanced = balance_with_fx_postings(
            unbal, fx_gain_loss_account_id=fx_acct, base_currency="USD"
        )
        assert balanced.is_balanced()
        # Two extra postings to the FX g/l account.
        fx_postings = [p for p in balanced.postings if p.account_id == fx_acct]
        assert len(fx_postings) == 2
        # Original postings preserved at the head.
        assert balanced.postings[: len(unbal.postings)] == unbal.postings
