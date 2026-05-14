"""Tests for transaction-level notes round-trip + clearing (issue #271)."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

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
    Transaction as TxModel,
)
from tulip_storage.repositories import (
    AccountRepository,
    TransactionRepository,
)
from tulip_storage.repositories.transaction import (
    UNSET,
    MasterKeyRequiredError,
)


@pytest.fixture
def household(session: Session) -> Household:
    """Seed a household for the test session."""
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


@pytest.fixture
def master_key() -> bytes:
    """Random 32-byte AES-256 key, fresh per test."""
    return os.urandom(32)


def _seed_accounts(session: Session, household: Household) -> tuple[AccountModel, AccountModel]:
    repo = AccountRepository(session, household.id)
    cash = repo.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
    food = repo.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
    session.commit()
    return cash, food


def _balanced_postings(
    food: AccountModel, cash: AccountModel
) -> tuple[DomainPosting, DomainPosting]:
    return (
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
    )


def _lunch_domain(
    household: Household,
    food: AccountModel,
    cash: AccountModel,
    *,
    status: DomainTxStatus = DomainTxStatus.PENDING,
) -> DomainTransaction:
    return DomainTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 6, 1),
        description="Lunch",
        postings=_balanced_postings(food, cash),
        status=status,
    )


class TestSaveBalancedNotes:
    def test_save_with_notes_round_trips(
        self, session: Session, household: Household, master_key: bytes
    ):
        cash, food = _seed_accounts(session, household)
        domain_tx = _lunch_domain(household, food, cash)
        repo = TransactionRepository(session, household.id, master_key=master_key)

        plaintext = "Reimbursed by Carol on 1/15.\nSee email thread."
        saved = repo.save_balanced(domain_tx, notes=plaintext)
        session.commit()

        loaded = repo.get(saved.id)
        assert loaded is not None
        assert repo.decrypt_notes(loaded) == plaintext

    def test_save_without_notes_leaves_column_null(
        self, session: Session, household: Household, master_key: bytes
    ):
        cash, food = _seed_accounts(session, household)
        domain_tx = _lunch_domain(household, food, cash)
        repo = TransactionRepository(session, household.id, master_key=master_key)

        saved = repo.save_balanced(domain_tx)
        session.commit()

        loaded = session.query(TxModel).filter_by(id=saved.id).one()
        assert loaded.notes_encrypted is None
        assert repo.decrypt_notes(loaded) is None

    def test_save_with_notes_stores_ciphertext_not_plaintext(
        self, session: Session, household: Household, master_key: bytes
    ):
        """Encrypted-at-rest invariant: raw row bytes never equal the plaintext."""
        cash, food = _seed_accounts(session, household)
        domain_tx = _lunch_domain(household, food, cash)
        repo = TransactionRepository(session, household.id, master_key=master_key)

        plaintext = "Sensitive memo about the transaction"
        saved = repo.save_balanced(domain_tx, notes=plaintext)
        session.commit()

        # Re-query the row directly so we observe the on-disk ciphertext.
        session.expire_all()
        raw = session.query(TxModel).filter_by(id=saved.id).one()
        assert raw.notes_encrypted is not None
        assert raw.notes_encrypted != plaintext.encode("utf-8")
        assert plaintext.encode("utf-8") not in raw.notes_encrypted

    def test_save_with_notes_requires_master_key(self, session: Session, household: Household):
        cash, food = _seed_accounts(session, household)
        domain_tx = _lunch_domain(household, food, cash)
        repo = TransactionRepository(session, household.id)  # no master_key

        with pytest.raises(MasterKeyRequiredError):
            repo.save_balanced(domain_tx, notes="oops")

    def test_save_without_notes_works_without_master_key(
        self, session: Session, household: Household
    ):
        """Existing callers that don't touch notes shouldn't need a key."""
        cash, food = _seed_accounts(session, household)
        domain_tx = _lunch_domain(household, food, cash)
        repo = TransactionRepository(session, household.id)

        saved = repo.save_balanced(domain_tx)
        session.commit()
        assert saved.notes_encrypted is None


class TestUpdatePendingNotes:
    def _save_pending(
        self,
        session: Session,
        household: Household,
        food: AccountModel,
        cash: AccountModel,
        master_key: bytes,
        *,
        initial_notes: str | None = None,
    ) -> TxModel:
        domain_tx = _lunch_domain(household, food, cash)
        repo = TransactionRepository(session, household.id, master_key=master_key)
        saved = repo.save_balanced(domain_tx, notes=initial_notes)
        session.commit()
        return saved

    def test_update_pending_sets_notes(
        self, session: Session, household: Household, master_key: bytes
    ):
        cash, food = _seed_accounts(session, household)
        saved = self._save_pending(session, household, food, cash, master_key)
        repo = TransactionRepository(session, household.id, master_key=master_key)

        repo.update_pending(
            saved.id,
            date=saved.date,
            description=saved.description,
            reference=saved.reference,
            postings=_balanced_postings(food, cash),
            notes="Added later",
        )
        session.commit()

        loaded = repo.get(saved.id)
        assert loaded is not None
        assert repo.decrypt_notes(loaded) == "Added later"

    def test_update_pending_clears_notes_with_explicit_none(
        self, session: Session, household: Household, master_key: bytes
    ):
        cash, food = _seed_accounts(session, household)
        saved = self._save_pending(
            session, household, food, cash, master_key, initial_notes="will be cleared"
        )
        repo = TransactionRepository(session, household.id, master_key=master_key)

        repo.update_pending(
            saved.id,
            date=saved.date,
            description=saved.description,
            reference=saved.reference,
            postings=_balanced_postings(food, cash),
            notes=None,
        )
        session.commit()

        session.expire_all()
        loaded = session.query(TxModel).filter_by(id=saved.id).one()
        assert loaded.notes_encrypted is None

    def test_update_pending_with_unset_preserves_notes(
        self, session: Session, household: Household, master_key: bytes
    ):
        cash, food = _seed_accounts(session, household)
        saved = self._save_pending(
            session, household, food, cash, master_key, initial_notes="keep me"
        )
        repo = TransactionRepository(session, household.id, master_key=master_key)

        # Update other fields; pass UNSET (the default) for notes.
        repo.update_pending(
            saved.id,
            date=saved.date,
            description="Lunch (renamed)",
            reference=saved.reference,
            postings=_balanced_postings(food, cash),
            notes=UNSET,
        )
        session.commit()

        loaded = repo.get(saved.id)
        assert loaded is not None
        assert repo.decrypt_notes(loaded) == "keep me"
