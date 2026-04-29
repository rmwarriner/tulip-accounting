"""Tests for tulip-storage repositories."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from tulip_core.money import Money
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import (
    Account as AccountModel,
)
from tulip_storage.models import (
    AccountType,
    AuditLog,
    Household,
    PeriodStatus,
)
from tulip_storage.repositories import (
    AccountRepository,
    AuditLogWriter,
    PeriodRepository,
    TransactionRepository,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


class TestAccountRepository:
    def test_create_and_list(self, session: Session, household: Household):
        repo = AccountRepository(session, household.id)
        a = repo.create(
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
        )
        assert a.id is not None
        assert a.household_id == household.id

        all_accounts = repo.list_active()
        assert len(all_accounts) == 1
        assert all_accounts[0].name == "Checking"

    def test_list_excludes_other_households(self, session: Session, household: Household):
        repo = AccountRepository(session, household.id)
        repo.create(code="1110", name="Mine", type=AccountType.ASSET, currency="USD")

        # Other household with its own account.
        other = Household(id=uuid4(), name="Jones", base_currency="USD")
        session.add(other)
        session.commit()
        other_repo = AccountRepository(session, other.id)
        other_repo.create(code="1110", name="Theirs", type=AccountType.ASSET, currency="USD")

        assert {a.name for a in repo.list_active()} == {"Mine"}
        assert {a.name for a in other_repo.list_active()} == {"Theirs"}

    def test_get_returns_none_for_other_household(self, session: Session, household: Household):
        a = AccountRepository(session, household.id).create(
            code="1110", name="Mine", type=AccountType.ASSET, currency="USD"
        )

        other = Household(id=uuid4(), name="Jones", base_currency="USD")
        session.add(other)
        session.commit()

        # Querying via the other household's repo must not return `a`.
        assert AccountRepository(session, other.id).get(a.id) is None

    def test_deactivate(self, session: Session, household: Household):
        repo = AccountRepository(session, household.id)
        a = repo.create(code="1110", name="X", type=AccountType.ASSET, currency="USD")
        repo.deactivate(a.id)
        session.commit()
        assert repo.list_active() == []
        # but get() still finds it
        assert repo.get(a.id) is not None


class TestPeriodRepository:
    def test_create_and_find_for_date(self, session: Session, household: Household):
        repo = PeriodRepository(session, household.id)
        p = repo.create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        assert repo.find_for_date(date(2026, 6, 1)) == p
        assert repo.find_for_date(date(2027, 1, 1)) is None

    def test_close_and_reopen(self, session: Session, household: Household):
        repo = PeriodRepository(session, household.id)
        p = repo.create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        admin = uuid4()
        repo.close(p.id, by_user_id=admin)
        session.commit()
        loaded = repo.get(p.id)
        assert loaded is not None
        assert loaded.status is PeriodStatus.SOFT_CLOSED
        assert loaded.closed_by_user_id == admin

        repo.reopen(p.id)
        session.commit()
        assert repo.get(p.id).status is PeriodStatus.OPEN  # type: ignore[union-attr]


class TestAuditLogWriter:
    def test_write_persists_row(self, session: Session, household: Household):
        writer = AuditLogWriter(session, household.id)
        writer.write(
            action="create",
            actor_kind="user",
            actor_user_id=None,
            entity_type="account",
            entity_id=uuid4(),
            before=None,
            after={"name": "Checking"},
        )
        session.commit()
        rows = session.query(AuditLog).all()
        assert len(rows) == 1
        assert rows[0].action == "create"
        assert rows[0].after_snapshot == {"name": "Checking"}


class TestTransactionRepository:
    def _seed_accounts(
        self, session: Session, household: Household
    ) -> tuple[AccountModel, AccountModel]:
        repo = AccountRepository(session, household.id)
        cash = repo.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
        food = repo.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
        session.commit()
        return cash, food

    def test_save_posted_transaction_with_postings(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        # Need an open period covering the date.
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=household.id,
            date=date(2026, 6, 1),
            description="Lunch",
            postings=(
                DomainPosting(
                    id=uuid4(), account_id=food.id, amount=Money(Decimal("12.50"), "USD")
                ),
                DomainPosting(
                    id=uuid4(), account_id=cash.id, amount=Money(Decimal("-12.50"), "USD")
                ),
            ),
            status=DomainTxStatus.POSTED,
        )
        TransactionRepository(session, household.id).save_balanced(domain_tx)
        session.commit()

        # Trigger should have validated; row exists with status POSTED.
        from tulip_storage.models import Posting as PostingModel
        from tulip_storage.models import Transaction as TxModel

        loaded = session.query(TxModel).filter_by(id=domain_tx.id).one()
        assert loaded.status.value == "posted"
        rows = session.query(PostingModel).filter_by(transaction_id=domain_tx.id).all()
        assert len(rows) == 2
        assert sum(r.amount for r in rows) == Decimal("0")

    def test_save_unbalanced_posted_aborts_via_trigger(
        self, session: Session, household: Household
    ):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        # Domain Transaction enforces balance for POSTED, so we have to
        # construct PENDING here and let the storage repository try (and
        # fail) the promotion. The point is that even if app code is wrong
        # the trigger catches it.
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=household.id,
            date=date(2026, 6, 1),
            description="Bad",
            postings=(
                DomainPosting(
                    id=uuid4(), account_id=food.id, amount=Money(Decimal("12.50"), "USD")
                ),
                DomainPosting(
                    id=uuid4(), account_id=cash.id, amount=Money(Decimal("-9.00"), "USD")
                ),
            ),
            status=DomainTxStatus.PENDING,
        )
        repo = TransactionRepository(session, household.id)
        with pytest.raises((IntegrityError, OperationalError), match="balance"):
            repo._force_post_unbalanced_for_test(domain_tx)
            session.commit()
