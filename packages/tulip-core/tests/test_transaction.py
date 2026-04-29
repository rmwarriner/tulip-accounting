"""Unit tests for Transaction value object."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.money import Money
from tulip_core.transactions import Posting, Transaction, TransactionStatus


def _balanced_postings() -> tuple[Posting, Posting]:
    return (
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
    )


class TestTransactionConstruction:
    def test_minimal_balanced_transaction(self):
        tx = Transaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 1, 15),
            description="Coffee",
            postings=_balanced_postings(),
            status=TransactionStatus.POSTED,
        )
        assert tx.description == "Coffee"
        assert tx.status is TransactionStatus.POSTED
        assert tx.is_balanced() is True

    def test_at_least_two_postings_required(self):
        with pytest.raises(ValueError, match="postings"):
            Transaction(
                id=uuid4(),
                household_id=uuid4(),
                date=date(2026, 1, 15),
                description="Single",
                postings=(
                    Posting(
                        id=uuid4(),
                        account_id=uuid4(),
                        amount=Money(Decimal("0"), "USD"),
                    ),
                ),
                status=TransactionStatus.PENDING,
            )

    def test_pending_unbalanced_is_allowed(self):
        # During import a transaction may be pending and unbalanced; balance
        # is required only when status is POSTED or RECONCILED.
        tx = Transaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 1, 15),
            description="From import",
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
        assert tx.is_balanced() is False

    def test_posted_unbalanced_raises(self):
        with pytest.raises(ValueError, match="balance"):
            Transaction(
                id=uuid4(),
                household_id=uuid4(),
                date=date(2026, 1, 15),
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
                status=TransactionStatus.POSTED,
            )


class TestBalancePerCurrency:
    def test_single_currency_balance(self):
        tx = Transaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 1, 15),
            description="USD",
            postings=_balanced_postings(),
            status=TransactionStatus.POSTED,
        )
        balances = tx.balance_per_currency()
        assert balances == {"USD": Decimal("0")}

    def test_multi_currency_balance(self):
        # USD postings sum to zero, EUR postings sum to zero, independently.
        tx = Transaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 1, 15),
            description="Multi",
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
                Posting(
                    id=uuid4(),
                    account_id=uuid4(),
                    amount=Money(Decimal("5.00"), "EUR"),
                ),
                Posting(
                    id=uuid4(),
                    account_id=uuid4(),
                    amount=Money(Decimal("-5.00"), "EUR"),
                ),
            ),
            status=TransactionStatus.POSTED,
        )
        assert tx.is_balanced() is True
        assert tx.balance_per_currency() == {"USD": Decimal("0"), "EUR": Decimal("0")}

    def test_multi_currency_unbalanced(self):
        # Each currency must independently sum to zero; USD imbalanced here.
        with pytest.raises(ValueError, match="balance"):
            Transaction(
                id=uuid4(),
                household_id=uuid4(),
                date=date(2026, 1, 15),
                description="Bad multi",
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
                    Posting(
                        id=uuid4(),
                        account_id=uuid4(),
                        amount=Money(Decimal("5.00"), "EUR"),
                    ),
                    Posting(
                        id=uuid4(),
                        account_id=uuid4(),
                        amount=Money(Decimal("-5.00"), "EUR"),
                    ),
                ),
                status=TransactionStatus.POSTED,
            )
