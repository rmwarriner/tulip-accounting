"""Tests for the P4.0 allocation/shadow-ledger migration.

Verifies that the migration adds the new tables, the shadow-ledger balance
trigger fires on POSTED transitions, and the long-deferred FK on
``postings.pool_id`` actually rejects orphan references.
"""

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
    AllocationPool,
    Household,
    PoolType,
    Posting,
    ShadowPosting,
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
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


def _seed_household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _seed_envelope_pool(session: Session, household_id) -> AllocationPool:
    p = AllocationPool(
        household_id=household_id,
        id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
        is_active=True,
        is_system=False,
    )
    session.add(p)
    session.commit()
    return p


def _seed_unallocated_system_pool(session: Session, household_id) -> AllocationPool:
    p = AllocationPool(
        household_id=household_id,
        id=uuid4(),
        pool_type=PoolType.UNALLOCATED,
        name="Unallocated USD",
        currency="USD",
        is_active=True,
        is_system=True,
    )
    session.add(p)
    session.commit()
    return p


class TestSchemaShape:
    def test_new_tables_exist_after_upgrade(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        names = set(inspect(eng).get_table_names())
        assert {
            "allocation_pools",
            "envelopes",
            "sinking_funds",
            "shadow_transactions",
            "shadow_postings",
        } <= names
        eng.dispose()

    def test_postings_pool_id_fk_added(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        fks = inspect(eng).get_foreign_keys("postings")
        # The FK is composite (household_id, pool_id) → allocation_pools.
        pool_fks = [fk for fk in fks if "pool_id" in fk["constrained_columns"]]
        assert pool_fks, f"expected FK on postings.pool_id, got {fks}"
        eng.dispose()

    def test_round_trip_upgrade_downgrade(self, tmp_path):
        db_path = tmp_path / "tulip.db"
        cfg = _make_alembic_cfg(f"sqlite:///{db_path}")
        upgrade(cfg, "head")
        downgrade(cfg, "base")
        from sqlalchemy import create_engine

        eng = create_engine(f"sqlite:///{db_path}")
        names = set(inspect(eng).get_table_names())
        assert names <= {"alembic_version"}
        eng.dispose()


class TestShadowBalanceTrigger:
    def test_balanced_shadow_post_succeeds(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            src = _seed_unallocated_system_pool(s, h.id)
            dst = _seed_envelope_pool(s, h.id)

            stx = ShadowTransaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Refill Groceries",
                reason=ShadowTxReason.REFILL,
                status=ShadowTxStatus.PENDING,
            )
            s.add(stx)
            s.flush()
            s.add_all(
                [
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=src.id,
                        amount=Decimal("-250"),
                        currency="USD",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=dst.id,
                        amount=Decimal("250"),
                        currency="USD",
                    ),
                ]
            )
            s.flush()
            stx.status = ShadowTxStatus.POSTED
            s.commit()

    def test_unbalanced_shadow_post_aborts(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            src = _seed_unallocated_system_pool(s, h.id)
            dst = _seed_envelope_pool(s, h.id)

            stx = ShadowTransaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Bad",
                reason=ShadowTxReason.REFILL,
                status=ShadowTxStatus.PENDING,
            )
            s.add(stx)
            s.flush()
            s.add_all(
                [
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=src.id,
                        amount=Decimal("-100"),
                        currency="USD",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=dst.id,
                        amount=Decimal("250"),
                        currency="USD",
                    ),
                ]
            )
            s.flush()
            stx.status = ShadowTxStatus.POSTED
            with pytest.raises((IntegrityError, OperationalError), match="balance"):
                s.commit()

    def test_inserting_unbalanced_posting_into_posted_shadow_tx_aborts(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            src = _seed_unallocated_system_pool(s, h.id)
            dst = _seed_envelope_pool(s, h.id)

            stx = ShadowTransaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Refill",
                reason=ShadowTxReason.REFILL,
                status=ShadowTxStatus.PENDING,
            )
            s.add(stx)
            s.flush()
            s.add_all(
                [
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=src.id,
                        amount=Decimal("-250"),
                        currency="USD",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=dst.id,
                        amount=Decimal("250"),
                        currency="USD",
                    ),
                ]
            )
            s.flush()
            stx.status = ShadowTxStatus.POSTED
            s.commit()

            with Smaker() as s2:
                bad = ShadowPosting(
                    id=uuid4(),
                    household_id=h.id,
                    shadow_transaction_id=stx.id,
                    pool_id=dst.id,
                    amount=Decimal("1"),
                    currency="USD",
                )
                s2.add(bad)
                with pytest.raises((IntegrityError, OperationalError), match="balance"):
                    s2.commit()

    def test_pending_shadow_tx_can_be_unbalanced(self, migrated_db):
        # PENDING is exempt from the balance trigger; only the transition
        # into POSTED enforces zero-sum.
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            src = _seed_unallocated_system_pool(s, h.id)
            dst = _seed_envelope_pool(s, h.id)

            stx = ShadowTransaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="WIP",
                reason=ShadowTxReason.MANUAL,
                status=ShadowTxStatus.PENDING,
            )
            s.add(stx)
            s.flush()
            s.add_all(
                [
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=src.id,
                        amount=Decimal("-100"),
                        currency="USD",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=dst.id,
                        amount=Decimal("50"),
                        currency="USD",
                    ),
                ]
            )
            s.commit()

    def test_per_currency_balance_segregated(self, migrated_db):
        # USD legs zero, EUR legs zero. Mixed currencies in one shadow tx
        # are valid as long as each currency individually balances.
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            usd_src = _seed_unallocated_system_pool(s, h.id)
            usd_dst = _seed_envelope_pool(s, h.id)
            eur_src = AllocationPool(
                household_id=h.id,
                id=uuid4(),
                pool_type=PoolType.UNALLOCATED,
                name="Unallocated EUR",
                currency="EUR",
                is_active=True,
                is_system=True,
            )
            eur_dst = AllocationPool(
                household_id=h.id,
                id=uuid4(),
                pool_type=PoolType.ENVELOPE,
                name="Travel",
                currency="EUR",
                is_active=True,
                is_system=False,
            )
            s.add_all([eur_src, eur_dst])
            s.commit()

            stx = ShadowTransaction(
                household_id=h.id,
                id=uuid4(),
                date=date(2026, 6, 1),
                description="Multi-ccy",
                reason=ShadowTxReason.MANUAL,
                status=ShadowTxStatus.PENDING,
            )
            s.add(stx)
            s.flush()
            s.add_all(
                [
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=usd_src.id,
                        amount=Decimal("-100"),
                        currency="USD",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=usd_dst.id,
                        amount=Decimal("100"),
                        currency="USD",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=eur_src.id,
                        amount=Decimal("-50"),
                        currency="EUR",
                    ),
                    ShadowPosting(
                        id=uuid4(),
                        household_id=h.id,
                        shadow_transaction_id=stx.id,
                        pool_id=eur_dst.id,
                        amount=Decimal("50"),
                        currency="EUR",
                    ),
                ]
            )
            s.flush()
            stx.status = ShadowTxStatus.POSTED
            s.commit()


class TestPoolIdForeignKeyOnPostings:
    def test_orphan_pool_id_on_posting_rejected(self, migrated_db):
        # Insert an account, a transaction, and a posting that points its
        # `pool_id` at a pool UUID that doesn't exist in `allocation_pools`.
        # The FK added by P4.0 must reject the posting.
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
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
            s.add_all([cash, food])
            s.commit()
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
                        pool_id=uuid4(),  # orphan — no matching allocation_pool
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
            with pytest.raises(IntegrityError):
                s.commit()
