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

    def test_get_by_code_returns_match(self, session: Session, household: Household):
        repo = AccountRepository(session, household.id)
        a = repo.create(
            code="Imbalance:Unknown",
            name="Imbalance: Unknown",
            type=AccountType.EQUITY,
            currency="USD",
        )
        found = repo.get_by_code("Imbalance:Unknown")
        assert found is not None
        assert found.id == a.id

    def test_get_by_code_returns_none_when_missing(self, session: Session, household: Household):
        repo = AccountRepository(session, household.id)
        repo.create(code="1110", name="Checking", type=AccountType.ASSET, currency="USD")
        assert repo.get_by_code("DoesNotExist") is None

    def test_get_by_code_scoped_to_household(self, session: Session, household: Household):
        # One household has the code; the other does not.
        AccountRepository(session, household.id).create(
            code="Imbalance:Unknown",
            name="Imbalance",
            type=AccountType.EQUITY,
            currency="USD",
        )
        other = Household(id=uuid4(), name="Jones", base_currency="USD")
        session.add(other)
        session.commit()
        assert AccountRepository(session, other.id).get_by_code("Imbalance:Unknown") is None


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

    def test_save_balanced_persists_imported_from_id(self, session: Session, household: Household):
        """save_balanced(..., imported_from_id=...) propagates to the header."""
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        batch_id = uuid4()
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=household.id,
            date=date(2026, 6, 1),
            description="Promoted from statement line",
            postings=(
                DomainPosting(id=uuid4(), account_id=food.id, amount=Money(Decimal("8.00"), "USD")),
                DomainPosting(
                    id=uuid4(), account_id=cash.id, amount=Money(Decimal("-8.00"), "USD")
                ),
            ),
            status=DomainTxStatus.PENDING,
        )
        header = TransactionRepository(session, household.id).save_balanced(
            domain_tx, imported_from_id=batch_id
        )
        assert header.imported_from_id == batch_id

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

    def _post_tx(
        self,
        session: Session,
        household: Household,
        *,
        tx_date: date,
        debit_account: AccountModel,
        credit_account: AccountModel,
        amount: Decimal,
        currency: str = "USD",
        status: DomainTxStatus = DomainTxStatus.POSTED,
    ) -> None:
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=household.id,
            date=tx_date,
            description=f"{amount} on {tx_date}",
            postings=(
                DomainPosting(
                    id=uuid4(),
                    account_id=debit_account.id,
                    amount=Money(amount, currency),
                ),
                DomainPosting(
                    id=uuid4(),
                    account_id=credit_account.id,
                    amount=Money(-amount, currency),
                ),
            ),
            status=status,
        )
        TransactionRepository(session, household.id).save_balanced(domain_tx)
        session.commit()

    def test_balance_for_account_sums_posted_amounts(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        # Two grocery runs.
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("12.50"),
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 2),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("7.25"),
        )

        repo = TransactionRepository(session, household.id)
        assert repo.balance_for_account(food.id, currency="USD") == Decimal("19.75")
        assert repo.balance_for_account(cash.id, currency="USD") == Decimal("-19.75")

    def test_balance_for_account_excludes_pending(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("10.00"),
            status=DomainTxStatus.POSTED,
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 2),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("99.00"),
            status=DomainTxStatus.PENDING,
        )

        repo = TransactionRepository(session, household.id)
        assert repo.balance_for_account(food.id, currency="USD") == Decimal("10.00")

    def test_balance_for_account_filters_to_currency(self, session: Session, household: Household):
        repo_a = AccountRepository(session, household.id)
        usd_a = repo_a.create(code="A", name="A", type=AccountType.ASSET, currency="USD")
        usd_b = repo_a.create(code="B", name="B", type=AccountType.ASSET, currency="USD")
        eur_a = repo_a.create(code="EA", name="EA", type=AccountType.ASSET, currency="EUR")
        eur_b = repo_a.create(code="EB", name="EB", type=AccountType.ASSET, currency="EUR")
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        # USD-only transaction: $5 from usd_a to usd_b.
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=usd_b,
            credit_account=usd_a,
            amount=Decimal("5.00"),
            currency="USD",
        )
        # EUR-only transaction: €3 from eur_a to eur_b.
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 2),
            debit_account=eur_b,
            credit_account=eur_a,
            amount=Decimal("3.00"),
            currency="EUR",
        )

        repo = TransactionRepository(session, household.id)
        # USD query should not pick up EUR postings (and vice versa).
        assert repo.balance_for_account(usd_b.id, currency="USD") == Decimal("5.00")
        assert repo.balance_for_account(eur_b.id, currency="EUR") == Decimal("3.00")
        assert repo.balance_for_account(usd_b.id, currency="EUR") == Decimal("0")

    def test_balance_for_account_respects_as_of(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        self._post_tx(
            session,
            household,
            tx_date=date(2026, 1, 15),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("20.00"),
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("30.00"),
        )

        repo = TransactionRepository(session, household.id)
        assert repo.balance_for_account(food.id, currency="USD") == Decimal("50.00")
        assert repo.balance_for_account(food.id, currency="USD", as_of=date(2026, 5, 1)) == Decimal(
            "20.00"
        )
        assert repo.balance_for_account(
            food.id, currency="USD", as_of=date(2025, 12, 31)
        ) == Decimal("0")

    def test_trial_balance_groups_by_account_and_currency(
        self, session: Session, household: Household
    ):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("12.50"),
        )

        repo = TransactionRepository(session, household.id)
        rows = sorted(repo.trial_balance(), key=lambda r: r.account_id.bytes)
        balances_by_account = {r.account_id: (r.currency, r.balance) for r in rows}
        assert balances_by_account[food.id] == ("USD", Decimal("12.50"))
        assert balances_by_account[cash.id] == ("USD", Decimal("-12.50"))

    def test_trial_balance_excludes_pending_and_other_households(
        self, session: Session, household: Household
    ):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        # PENDING — should be excluded.
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("99.00"),
            status=DomainTxStatus.PENDING,
        )

        # POSTED — should appear.
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("10.00"),
            status=DomainTxStatus.POSTED,
        )

        # Different household; should not leak.
        other = Household(id=uuid4(), name="Jones", base_currency="USD")
        session.add(other)
        session.commit()
        other_repo = AccountRepository(session, other.id)
        oc = other_repo.create(code="X", name="X", type=AccountType.ASSET, currency="USD")
        of = other_repo.create(code="Y", name="Y", type=AccountType.EXPENSE, currency="USD")
        PeriodRepository(session, other.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            other,
            tx_date=date(2026, 6, 1),
            debit_account=of,
            credit_account=oc,
            amount=Decimal("777.00"),
            status=DomainTxStatus.POSTED,
        )

        rows = TransactionRepository(session, household.id).trial_balance()
        balances_by_account = {r.account_id: r.balance for r in rows}
        # Only the POSTED USD tx in the household, not the PENDING and not the other household.
        assert balances_by_account.get(food.id) == Decimal("10.00")
        assert balances_by_account.get(cash.id) == Decimal("-10.00")
        assert oc.id not in balances_by_account
        assert of.id not in balances_by_account

    def test_trial_balance_respects_as_of(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()

        self._post_tx(
            session,
            household,
            tx_date=date(2026, 1, 15),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("5.00"),
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 8, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("7.00"),
        )

        repo = TransactionRepository(session, household.id)
        early = {r.account_id: r.balance for r in repo.trial_balance(as_of=date(2026, 5, 1))}
        late = {r.account_id: r.balance for r in repo.trial_balance()}
        assert early[food.id] == Decimal("5.00")
        assert late[food.id] == Decimal("12.00")

    def _seed_posted_and_pending(
        self, session: Session, household: Household
    ) -> tuple[AccountModel, AccountModel]:
        """Seed one POSTED (10.00) and one PENDING (99.00) grocery run."""
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("10.00"),
            status=DomainTxStatus.POSTED,
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 2),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("99.00"),
            status=DomainTxStatus.PENDING,
        )
        return cash, food

    def test_balance_for_account_include_pending_folds_in_pending(
        self, session: Session, household: Household
    ):
        # #274: include_pending widens the sum to PENDING transactions.
        _cash, food = self._seed_posted_and_pending(session, household)
        repo = TransactionRepository(session, household.id)
        assert repo.balance_for_account(food.id, currency="USD") == Decimal("10.00")
        assert repo.balance_for_account(food.id, currency="USD", include_pending=True) == Decimal(
            "109.00"
        )

    def test_count_pending_for_account(self, session: Session, household: Household):
        _cash, food = self._seed_posted_and_pending(session, household)
        repo = TransactionRepository(session, household.id)
        assert repo.count_pending_for_account(food.id, currency="USD") == 1

    def test_trial_balance_include_pending_folds_in_and_flags_rows(
        self, session: Session, household: Household
    ):
        # #274: include_pending sums PENDING in and marks affected rows.
        _cash, food = self._seed_posted_and_pending(session, household)
        repo = TransactionRepository(session, household.id)

        posted_only = {r.account_id: r for r in repo.trial_balance()}
        assert posted_only[food.id].balance == Decimal("10.00")
        assert posted_only[food.id].has_pending is False

        with_pending = {r.account_id: r for r in repo.trial_balance(include_pending=True)}
        assert with_pending[food.id].balance == Decimal("109.00")
        assert with_pending[food.id].has_pending is True

    def test_count_pending_transactions(self, session: Session, household: Household):
        self._seed_posted_and_pending(session, household)
        repo = TransactionRepository(session, household.id)
        assert repo.count_pending_transactions() == 1
        # as_of before the pending tx → zero.
        assert repo.count_pending_transactions(as_of=date(2026, 6, 1)) == 0

    def test_list_headers_orders_by_date_desc(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        for tx_date, amount in [
            (date(2026, 1, 5), Decimal("1.00")),
            (date(2026, 6, 1), Decimal("2.00")),
            (date(2026, 3, 15), Decimal("3.00")),
        ]:
            self._post_tx(
                session,
                household,
                tx_date=tx_date,
                debit_account=food,
                credit_account=cash,
                amount=amount,
            )

        headers = TransactionRepository(session, household.id).list_headers()
        assert [h.date for h in headers] == [
            date(2026, 6, 1),
            date(2026, 3, 15),
            date(2026, 1, 5),
        ]

    def test_list_headers_filters_by_account(self, session: Session, household: Household):
        repo_a = AccountRepository(session, household.id)
        cash = repo_a.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
        food = repo_a.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
        rent = repo_a.create(code="5200", name="Rent", type=AccountType.EXPENSE, currency="USD")
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("12.50"),
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 2),
            debit_account=rent,
            credit_account=cash,
            amount=Decimal("1500.00"),
        )

        repo = TransactionRepository(session, household.id)
        # Cash sits on both → both transactions returned.
        assert len(repo.list_headers(account_id=cash.id)) == 2
        # Food only on the lunch one.
        food_only = repo.list_headers(account_id=food.id)
        assert len(food_only) == 1
        assert food_only[0].description.startswith("12.50")
        # Rent only on the rent one.
        rent_only = repo.list_headers(account_id=rent.id)
        assert len(rent_only) == 1
        assert rent_only[0].description.startswith("1500.00")

    def test_list_headers_filters_by_date_range(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        for tx_date in [date(2026, 1, 15), date(2026, 6, 15), date(2026, 11, 15)]:
            self._post_tx(
                session,
                household,
                tx_date=tx_date,
                debit_account=food,
                credit_account=cash,
                amount=Decimal("10.00"),
            )

        repo = TransactionRepository(session, household.id)
        from_only = repo.list_headers(from_date=date(2026, 6, 1))
        assert {h.date for h in from_only} == {date(2026, 6, 15), date(2026, 11, 15)}
        to_only = repo.list_headers(to_date=date(2026, 6, 30))
        assert {h.date for h in to_only} == {date(2026, 1, 15), date(2026, 6, 15)}
        ranged = repo.list_headers(from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))
        assert {h.date for h in ranged} == {date(2026, 6, 15)}

    def test_list_headers_filters_by_status(self, session: Session, household: Household):
        from tulip_storage.models import TransactionStatus

        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("10.00"),
            status=DomainTxStatus.POSTED,
        )
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 2),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("99.00"),
            status=DomainTxStatus.PENDING,
        )

        repo = TransactionRepository(session, household.id)
        posted = repo.list_headers(status=TransactionStatus.POSTED)
        pending = repo.list_headers(status=TransactionStatus.PENDING)
        assert len(posted) == 1 and posted[0].status is TransactionStatus.POSTED
        assert len(pending) == 1 and pending[0].status is TransactionStatus.PENDING

    def test_list_headers_filters_by_id_prefix(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        for day in (1, 2, 3):
            self._post_tx(
                session,
                household,
                tx_date=date(2026, 6, day),
                debit_account=food,
                credit_account=cash,
                amount=Decimal("1.00"),
            )

        repo = TransactionRepository(session, household.id)
        all_headers = repo.list_headers()
        assert len(all_headers) == 3
        target = all_headers[0]
        prefix = str(target.id)[:8]

        matched = repo.list_headers(id_prefix=prefix)
        assert [h.id for h in matched] == [target.id]

        # Case-insensitive: stored ids are lowercase, but a mixed-case prefix
        # should still resolve.
        upper = repo.list_headers(id_prefix=prefix.upper())
        assert [h.id for h in upper] == [target.id]

        # Garbage prefix returns nothing rather than blowing up.
        assert repo.list_headers(id_prefix="deadbeef") == []

    def test_list_headers_respects_limit(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        for day in (1, 2, 3, 4, 5):
            self._post_tx(
                session,
                household,
                tx_date=date(2026, 6, day),
                debit_account=food,
                credit_account=cash,
                amount=Decimal("1.00"),
            )

        repo = TransactionRepository(session, household.id)
        # Limit returns the newest N (date desc).
        top_two = repo.list_headers(limit=2)
        assert [h.date for h in top_two] == [date(2026, 6, 5), date(2026, 6, 4)]

    def test_list_headers_excludes_other_households(self, session: Session, household: Household):
        cash, food = self._seed_accounts(session, household)
        PeriodRepository(session, household.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            household,
            tx_date=date(2026, 6, 1),
            debit_account=food,
            credit_account=cash,
            amount=Decimal("10.00"),
        )

        # A second household with its own accounts and transaction.
        other = Household(id=uuid4(), name="Jones", base_currency="USD")
        session.add(other)
        session.commit()
        other_repo = AccountRepository(session, other.id)
        other_cash = other_repo.create(
            code="1110", name="Cash", type=AccountType.ASSET, currency="USD"
        )
        other_food = other_repo.create(
            code="5100", name="Food", type=AccountType.EXPENSE, currency="USD"
        )
        PeriodRepository(session, other.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.commit()
        self._post_tx(
            session,
            other,
            tx_date=date(2026, 6, 1),
            debit_account=other_food,
            credit_account=other_cash,
            amount=Decimal("99.99"),
        )

        ours = TransactionRepository(session, household.id).list_headers()
        theirs = TransactionRepository(session, other.id).list_headers()
        assert len(ours) == 1
        assert len(theirs) == 1
        assert ours[0].id != theirs[0].id

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
