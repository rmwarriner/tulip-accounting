"""Tests for P5.0 TransactionRepository void / edit / delete additions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_core.accounting import build_reversal
from tulip_core.money import Money
from tulip_core.transactions import Posting as DomainPosting
from tulip_core.transactions import Transaction as DomainTransaction
from tulip_core.transactions import TransactionStatus as DomainTxStatus
from tulip_storage.models import (
    Account as AccountModel,
)
from tulip_storage.models import (
    AccountType,
    Household,
)
from tulip_storage.models import (
    Posting as PostingModel,
)
from tulip_storage.models import (
    Transaction as TxModel,
)
from tulip_storage.repositories import (
    AccountRepository,
    TransactionRepository,
)
from tulip_storage.repositories.transaction import (
    TransactionAlreadyVoidedError,
    TransactionNotDeletableError,
    TransactionNotEditableError,
    TransactionNotVoidableError,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _seed_accounts(session: Session, household: Household) -> tuple[AccountModel, AccountModel]:
    repo = AccountRepository(session, household.id)
    cash = repo.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
    food = repo.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
    session.commit()
    return cash, food


def _post_lunch(
    session: Session, household: Household, food: AccountModel, cash: AccountModel
) -> DomainTransaction:
    domain_tx = DomainTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 6, 1),
        description="Lunch",
        postings=(
            DomainPosting(id=uuid4(), account_id=food.id, amount=Money(Decimal("12.50"), "USD")),
            DomainPosting(id=uuid4(), account_id=cash.id, amount=Money(Decimal("-12.50"), "USD")),
        ),
        status=DomainTxStatus.POSTED,
    )
    TransactionRepository(session, household.id).save_balanced(domain_tx)
    session.commit()
    return domain_tx


def _post_pending_lunch(
    session: Session, household: Household, food: AccountModel, cash: AccountModel
) -> DomainTransaction:
    domain_tx = DomainTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 6, 1),
        description="Lunch (pending)",
        postings=(
            DomainPosting(id=uuid4(), account_id=food.id, amount=Money(Decimal("12.50"), "USD")),
            DomainPosting(id=uuid4(), account_id=cash.id, amount=Money(Decimal("-12.50"), "USD")),
        ),
        status=DomainTxStatus.PENDING,
    )
    TransactionRepository(session, household.id).save_balanced(domain_tx)
    session.commit()
    return domain_tx


class TestPersistReversal:
    def test_links_source_and_persists_sibling(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        source = _post_lunch(session, household, food, cash)

        reversal = build_reversal(
            source,
            reversal_id=uuid4(),
            reversal_date=date(2026, 7, 1),
            description="Reversal: duplicate charge",
        )
        # Engine would normally validate period; for repo test, we set the
        # status directly to POSTED since period validation is the API
        # layer's concern.
        from dataclasses import replace

        reversal_posted = replace(reversal, status=DomainTxStatus.POSTED)

        repo = TransactionRepository(session, household.id)
        voided_at = datetime.now(tz=UTC)
        result = repo.persist_reversal(source.id, reversal_posted, voided_at=voided_at)
        session.commit()

        # Reversal row exists with POSTED status.
        loaded_reversal = session.query(TxModel).filter_by(id=reversal_posted.id).one()
        assert loaded_reversal.status.value == "posted"
        assert result.id == reversal_posted.id

        # Reversal postings are sign-flipped.
        rev_postings = (
            session.query(PostingModel).filter_by(transaction_id=reversal_posted.id).all()
        )
        rev_by_acct = {p.account_id: p.amount for p in rev_postings}
        assert rev_by_acct[food.id] == Decimal("-12.50")
        assert rev_by_acct[cash.id] == Decimal("12.50")

        # Source's voided_by_transaction_id is set.
        loaded_source = session.query(TxModel).filter_by(id=source.id).one()
        assert loaded_source.voided_by_transaction_id == reversal_posted.id
        assert loaded_source.voided_at is not None

    def test_already_voided_source_raises(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        source = _post_lunch(session, household, food, cash)

        from dataclasses import replace

        first = replace(
            build_reversal(
                source,
                reversal_id=uuid4(),
                reversal_date=date(2026, 7, 1),
                description="First reversal",
            ),
            status=DomainTxStatus.POSTED,
        )
        repo = TransactionRepository(session, household.id)
        repo.persist_reversal(source.id, first, voided_at=datetime.now(tz=UTC))
        session.commit()

        # Second attempt: source is already voided.
        second = replace(
            build_reversal(
                source,
                reversal_id=uuid4(),
                reversal_date=date(2026, 7, 2),
                description="Second reversal (should fail)",
            ),
            status=DomainTxStatus.POSTED,
        )
        with pytest.raises(TransactionAlreadyVoidedError):
            repo.persist_reversal(source.id, second, voided_at=datetime.now(tz=UTC))

    def test_pending_source_raises_not_voidable(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        source = _post_pending_lunch(session, household, food, cash)

        from dataclasses import replace

        # Build a stand-in reversal — the repo should reject before persistence.
        bogus_reversal = DomainTransaction(
            id=uuid4(),
            household_id=household.id,
            date=date(2026, 7, 1),
            description="Bogus",
            postings=(
                DomainPosting(
                    id=uuid4(), account_id=food.id, amount=Money(Decimal("-12.50"), "USD")
                ),
                DomainPosting(
                    id=uuid4(), account_id=cash.id, amount=Money(Decimal("12.50"), "USD")
                ),
            ),
            status=DomainTxStatus.POSTED,
        )
        del replace  # unused in this branch
        repo = TransactionRepository(session, household.id)
        with pytest.raises(TransactionNotVoidableError):
            repo.persist_reversal(source.id, bogus_reversal, voided_at=datetime.now(tz=UTC))

    def test_unknown_source_raises_lookup_error(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        bogus = DomainTransaction(
            id=uuid4(),
            household_id=household.id,
            date=date(2026, 7, 1),
            description="Bogus",
            postings=(
                DomainPosting(
                    id=uuid4(), account_id=food.id, amount=Money(Decimal("-12.50"), "USD")
                ),
                DomainPosting(
                    id=uuid4(), account_id=cash.id, amount=Money(Decimal("12.50"), "USD")
                ),
            ),
            status=DomainTxStatus.POSTED,
        )
        repo = TransactionRepository(session, household.id)
        with pytest.raises(LookupError):
            repo.persist_reversal(uuid4(), bogus, voided_at=datetime.now(tz=UTC))


class TestDeletePending:
    def test_pending_tx_deleted_with_postings(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        source = _post_pending_lunch(session, household, food, cash)
        repo = TransactionRepository(session, household.id)
        repo.delete_pending(source.id)
        session.commit()

        assert session.query(TxModel).filter_by(id=source.id).one_or_none() is None
        assert session.query(PostingModel).filter_by(transaction_id=source.id).all() == []

    def test_posted_tx_raises(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        source = _post_lunch(session, household, food, cash)
        repo = TransactionRepository(session, household.id)
        with pytest.raises(TransactionNotDeletableError):
            repo.delete_pending(source.id)

    def test_unknown_tx_raises_lookup_error(self, session: Session, household: Household):
        repo = TransactionRepository(session, household.id)
        with pytest.raises(LookupError):
            repo.delete_pending(uuid4())


class TestUpdatePending:
    def test_updates_header_fields_and_replaces_postings(
        self, session: Session, household: Household
    ):
        cash, food = _seed_accounts(session, household)
        source = _post_pending_lunch(session, household, food, cash)
        repo = TransactionRepository(session, household.id)

        repo.update_pending(
            source.id,
            date=date(2026, 6, 2),
            description="Lunch (corrected)",
            reference="cc-9999",
            postings=(
                DomainPosting(
                    id=uuid4(),
                    account_id=food.id,
                    amount=Money(Decimal("15.00"), "USD"),
                ),
                DomainPosting(
                    id=uuid4(),
                    account_id=cash.id,
                    amount=Money(Decimal("-15.00"), "USD"),
                ),
            ),
        )
        session.commit()

        loaded = session.query(TxModel).filter_by(id=source.id).one()
        assert loaded.description == "Lunch (corrected)"
        assert loaded.reference == "cc-9999"
        assert loaded.date == date(2026, 6, 2)

        postings = session.query(PostingModel).filter_by(transaction_id=source.id).all()
        assert len(postings) == 2
        amounts = sorted(p.amount for p in postings)
        assert amounts == [Decimal("-15.00"), Decimal("15.00")]

    def test_pending_unbalanced_postings_allowed(self, session: Session, household: Household):
        # PENDING transactions may be unbalanced; the trigger only fires on
        # transitions into POSTED.
        cash, food = _seed_accounts(session, household)
        source = _post_pending_lunch(session, household, food, cash)
        repo = TransactionRepository(session, household.id)

        repo.update_pending(
            source.id,
            date=date(2026, 6, 2),
            description="WIP",
            reference=None,
            postings=(
                DomainPosting(
                    id=uuid4(),
                    account_id=food.id,
                    amount=Money(Decimal("15.00"), "USD"),
                ),
                DomainPosting(
                    id=uuid4(),
                    account_id=cash.id,
                    amount=Money(Decimal("-10.00"), "USD"),
                ),
            ),
        )
        session.commit()

    def test_posted_tx_raises(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        source = _post_lunch(session, household, food, cash)
        repo = TransactionRepository(session, household.id)
        with pytest.raises(TransactionNotEditableError):
            repo.update_pending(
                source.id,
                date=date(2026, 6, 2),
                description="too late",
                reference=None,
                postings=(
                    DomainPosting(
                        id=uuid4(),
                        account_id=food.id,
                        amount=Money(Decimal("12.50"), "USD"),
                    ),
                    DomainPosting(
                        id=uuid4(),
                        account_id=cash.id,
                        amount=Money(Decimal("-12.50"), "USD"),
                    ),
                ),
            )

    def test_unknown_tx_raises_lookup_error(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        repo = TransactionRepository(session, household.id)
        with pytest.raises(LookupError):
            repo.update_pending(
                uuid4(),
                date=date(2026, 6, 2),
                description="ghost",
                reference=None,
                postings=(
                    DomainPosting(
                        id=uuid4(),
                        account_id=food.id,
                        amount=Money(Decimal("12.50"), "USD"),
                    ),
                    DomainPosting(
                        id=uuid4(),
                        account_id=cash.id,
                        amount=Money(Decimal("-12.50"), "USD"),
                    ),
                ),
            )
