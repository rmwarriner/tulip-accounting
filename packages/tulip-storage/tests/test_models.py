"""Tests for tulip-storage model schema and basic CRUD."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tulip_storage.models import (
    Account,
    AccountType,
    AuditLog,
    Household,
    Period,
    PeriodStatus,
    Posting,
    Transaction,
    TransactionStatus,
    User,
    UserRole,
)


class TestSchemaIntrospection:
    def test_expected_tables_exist(self, engine):
        names = set(engine.dialect.get_table_names(engine.connect()))
        assert {
            "households",
            "users",
            "accounts",
            "periods",
            "transactions",
            "postings",
            "audit_log",
        } <= names


class TestHouseholdCrud:
    def test_create_and_read(self, session: Session):
        h = Household(id=uuid4(), name="Smith Family", base_currency="USD")
        session.add(h)
        session.commit()

        loaded = session.execute(select(Household)).scalar_one()
        assert loaded.name == "Smith Family"
        assert loaded.base_currency == "USD"


class TestUserCrud:
    def test_create_under_household(self, session: Session):
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        session.add(h)
        u = User(
            household_id=h.id,
            id=uuid4(),
            email="alice@example.com",
            password_hash="argon2id$dummy",
            display_name="Alice",
            role=UserRole.ADMIN,
        )
        session.add(u)
        session.commit()
        loaded = session.execute(select(User)).scalar_one()
        assert loaded.email == "alice@example.com"
        assert loaded.role is UserRole.ADMIN

    def test_user_household_id_must_match_existing_household(self, session: Session):
        # Insert user with no matching household → composite FK violation.
        u = User(
            household_id=uuid4(),
            id=uuid4(),
            email="ghost@example.com",
            password_hash="x",
            display_name="Ghost",
            role=UserRole.MEMBER,
        )
        session.add(u)
        with pytest.raises(IntegrityError):
            session.commit()


class TestAccountCrud:
    def test_round_trip_account(self, session: Session):
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        session.add(h)
        a = Account(
            household_id=h.id,
            id=uuid4(),
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
            visibility="shared",
        )
        session.add(a)
        session.commit()
        loaded = session.execute(select(Account)).scalar_one()
        assert loaded.code == "1110"
        assert loaded.type is AccountType.ASSET


class TestTransactionWithPostings:
    def _seed(self, session: Session) -> tuple[Household, Account, Account]:
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        session.add(h)
        cash = Account(
            household_id=h.id,
            id=uuid4(),
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
            visibility="shared",
        )
        food = Account(
            household_id=h.id,
            id=uuid4(),
            code="5100",
            name="Food",
            type=AccountType.EXPENSE,
            currency="USD",
            visibility="shared",
        )
        session.add_all([cash, food])
        session.commit()
        return h, cash, food

    def test_balanced_transaction_persists(self, session: Session):
        h, cash, food = self._seed(session)
        tx = Transaction(
            household_id=h.id,
            id=uuid4(),
            date=date(2026, 6, 1),
            description="Lunch",
            status=TransactionStatus.POSTED,
        )
        session.add(tx)
        session.flush()
        p1 = Posting(
            id=uuid4(),
            household_id=h.id,
            transaction_id=tx.id,
            account_id=food.id,
            amount=Decimal("12.50"),
            currency="USD",
        )
        p2 = Posting(
            id=uuid4(),
            household_id=h.id,
            transaction_id=tx.id,
            account_id=cash.id,
            amount=Decimal("-12.50"),
            currency="USD",
        )
        session.add_all([p1, p2])
        session.commit()

        loaded = (
            session.execute(select(Posting).where(Posting.transaction_id == tx.id)).scalars().all()
        )
        assert len(loaded) == 2
        assert sum(p.amount for p in loaded) == Decimal("0")

    def test_posting_with_mismatched_household_rejected(self, session: Session):
        h, _cash, food = self._seed(session)
        # Create a *different* household.
        other = Household(id=uuid4(), name="Jones", base_currency="USD")
        session.add(other)
        # Transaction belongs to `h`.
        tx = Transaction(
            household_id=h.id,
            id=uuid4(),
            date=date(2026, 6, 1),
            description="Bad",
            status=TransactionStatus.PENDING,
        )
        session.add(tx)
        session.flush()
        # Posting claims to belong to `other` while pointing at h's account
        # → composite FK mismatch.
        bad = Posting(
            id=uuid4(),
            household_id=other.id,
            transaction_id=tx.id,
            account_id=food.id,
            amount=Decimal("1.00"),
            currency="USD",
        )
        session.add(bad)
        with pytest.raises(IntegrityError):
            session.commit()


class TestPeriodAndAudit:
    def test_period_round_trip(self, session: Session):
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        session.add(h)
        p = Period(
            household_id=h.id,
            id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
        session.add(p)
        session.commit()
        assert session.execute(select(Period)).scalar_one().status is PeriodStatus.OPEN

    def test_audit_log_append(self, session: Session):
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        session.add(h)
        a = AuditLog(
            id=uuid4(),
            household_id=h.id,
            occurred_at=datetime.now(tz=UTC),
            actor_user_id=None,
            actor_kind="system",
            action="period_close",
            entity_type="period",
            entity_id=uuid4(),
            request_id=uuid4(),
        )
        session.add(a)
        session.commit()
        assert session.execute(select(AuditLog)).scalar_one().actor_kind == "system"
