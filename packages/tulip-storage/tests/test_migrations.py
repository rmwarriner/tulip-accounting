"""Tests for Alembic migrations and the balanced-postings trigger."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.models import (
    Account,
    AccountType,
    Household,
    Posting,
    Transaction,
    TransactionStatus,
)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(ALEMBIC_INI.parent / "src" / "tulip_storage" / "migrations"),
    )
    return cfg


@pytest.fixture
def migrated_db(tmp_path):
    """Yield a (db_url, sessionmaker) for a freshly migrated SQLite file."""
    db_path = tmp_path / "tulip.db"
    db_url = f"sqlite:///{db_path}"
    cfg = _make_alembic_cfg(db_url)
    upgrade(cfg, "head")

    from sqlalchemy import create_engine

    eng = create_engine(db_url, future=True)

    @event.listens_for(eng, "connect")
    def _enable_fk(dc, _r):  # type: ignore[no-untyped-def]
        cur = dc.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    yield db_url, sessionmaker(eng, expire_on_commit=False)
    eng.dispose()


class TestCompositeFkAiInvocationId:
    """#231: pending_proposals + notifications carry a composite FK to ai_invocations.

    Prevents cross-household references (a row in household A pointing at
    an invocation in household B) at the schema level.
    """

    def test_cross_household_ai_invocation_id_rejected_on_pending_proposals(self, migrated_db):
        from datetime import UTC, datetime

        from tulip_storage.models import AIInvocation, PendingProposal

        _, maker = migrated_db
        with maker() as s:
            household_a = Household(id=uuid4(), name="A", base_currency="USD")
            household_b = Household(id=uuid4(), name="B", base_currency="USD")
            s.add(household_a)
            s.add(household_b)
            s.flush()

            inv_b = AIInvocation(
                household_id=household_b.id,
                id=uuid4(),
                created_at=datetime.now(tz=UTC),
                capability="categorize",
                policy_resolved="default",
                profile="default",
                provider="ollama",
                model="llama3",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
                outcome="success",
                cost_estimate_usd=Decimal("0"),
                prompt_hash=b"\x00" * 32,
                actor_user_id=None,
                request_id=None,
                provider_response_id=None,
            )
            s.add(inv_b)
            s.flush()

            # Now try to land a proposal in household_a referencing inv_b.
            bad_proposal = PendingProposal(
                household_id=household_a.id,
                id=uuid4(),
                kind="envelope_budget_update",
                title="spoof",
                payload={"x": 1},
                created_by_kind="ai_agent",
                ai_invocation_id=inv_b.id,
            )
            s.add(bad_proposal)
            with pytest.raises(IntegrityError):
                s.flush()
            s.rollback()


class TestMigrationsRoundTrip:
    def test_upgrade_then_downgrade_is_clean(self, tmp_path):
        db_path = tmp_path / "tulip.db"
        cfg = _make_alembic_cfg(f"sqlite:///{db_path}")
        upgrade(cfg, "head")

        # Confirm tables exist.
        from sqlalchemy import create_engine

        eng = create_engine(f"sqlite:///{db_path}")
        names = set(inspect(eng).get_table_names())
        assert {
            "households",
            "users",
            "accounts",
            "periods",
            "transactions",
            "postings",
            "audit_log",
        } <= names
        eng.dispose()

        downgrade(cfg, "base")
        eng = create_engine(f"sqlite:///{db_path}")
        names = set(inspect(eng).get_table_names())
        # Only alembic's own bookkeeping table should remain.
        assert names <= {"alembic_version"}


class TestBalanceTrigger:
    def _seed_household_and_accounts(self, session: Session) -> tuple[Household, Account, Account]:
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

    def test_balanced_post_succeeds(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h, cash, food = self._seed_household_and_accounts(s)
            tx = Transaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Lunch",
                status=TransactionStatus.PENDING,
            )
            s.add(tx)
            s.flush()
            s.add_all(
                [
                    Posting(
                        id=uuid4(),
                        household_id=h.id,
                        transaction_id=tx.id,
                        account_id=food.id,
                        amount=Decimal("12.50"),
                        currency="USD",
                    ),
                    Posting(
                        id=uuid4(),
                        household_id=h.id,
                        transaction_id=tx.id,
                        account_id=cash.id,
                        amount=Decimal("-12.50"),
                        currency="USD",
                    ),
                ]
            )
            s.flush()
            # Promote to POSTED — trigger fires and validates balance.
            tx.status = TransactionStatus.POSTED
            s.commit()  # should succeed

    def test_unbalanced_post_aborts(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h, cash, food = self._seed_household_and_accounts(s)
            tx = Transaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Bad",
                status=TransactionStatus.PENDING,
            )
            s.add(tx)
            s.flush()
            s.add_all(
                [
                    Posting(
                        id=uuid4(),
                        household_id=h.id,
                        transaction_id=tx.id,
                        account_id=food.id,
                        amount=Decimal("12.50"),
                        currency="USD",
                    ),
                    Posting(
                        id=uuid4(),
                        household_id=h.id,
                        transaction_id=tx.id,
                        account_id=cash.id,
                        amount=Decimal("-9.00"),
                        currency="USD",
                    ),
                ]
            )
            s.flush()
            tx.status = TransactionStatus.POSTED
            with pytest.raises((IntegrityError, OperationalError), match="balance"):
                s.commit()

    def test_inserting_unbalanced_posting_into_posted_tx_aborts(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h, cash, food = self._seed_household_and_accounts(s)
            tx = Transaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Lunch",
                status=TransactionStatus.PENDING,
            )
            s.add(tx)
            s.flush()
            s.add_all(
                [
                    Posting(
                        id=uuid4(),
                        household_id=h.id,
                        transaction_id=tx.id,
                        account_id=food.id,
                        amount=Decimal("10.00"),
                        currency="USD",
                    ),
                    Posting(
                        id=uuid4(),
                        household_id=h.id,
                        transaction_id=tx.id,
                        account_id=cash.id,
                        amount=Decimal("-10.00"),
                        currency="USD",
                    ),
                ]
            )
            s.flush()
            tx.status = TransactionStatus.POSTED
            s.commit()

            # Now try to add a third posting that breaks balance.
            with Smaker() as s2:
                bad = Posting(
                    id=uuid4(),
                    household_id=h.id,
                    transaction_id=tx.id,
                    account_id=food.id,
                    amount=Decimal("1.00"),
                    currency="USD",
                )
                s2.add(bad)
                with pytest.raises((IntegrityError, OperationalError), match="balance"):
                    s2.commit()
