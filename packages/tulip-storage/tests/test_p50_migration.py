"""Tests for the P5.0 transaction-void migration.

Adds two columns to ``transactions``:
- ``voided_by_transaction_id``: nullable self-FK to the reversal sibling.
- ``voided_at``: nullable timestamp.
"""

from __future__ import annotations

from datetime import UTC, date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError
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


def _seed_balanced_tx(session: Session, household_id) -> Transaction:
    cash = Account(
        household_id=household_id,
        id=uuid4(),
        code="1110",
        name="Checking",
        type=AccountType.ASSET,
        currency="USD",
        visibility="shared",
    )
    food = Account(
        household_id=household_id,
        id=uuid4(),
        code="5100",
        name="Food",
        type=AccountType.EXPENSE,
        currency="USD",
        visibility="shared",
    )
    session.add_all([cash, food])
    session.commit()
    tx = Transaction(
        household_id=household_id,
        id=uuid4(),
        date=date(2026, 6, 1),
        description="Lunch",
        status=TransactionStatus.PENDING,
    )
    session.add(tx)
    session.flush()
    session.add_all(
        [
            Posting(
                id=uuid4(),
                household_id=household_id,
                transaction_id=tx.id,
                account_id=food.id,
                amount=Decimal("12.50"),
                currency="USD",
            ),
            Posting(
                id=uuid4(),
                household_id=household_id,
                transaction_id=tx.id,
                account_id=cash.id,
                amount=Decimal("-12.50"),
                currency="USD",
            ),
        ]
    )
    session.flush()
    tx.status = TransactionStatus.POSTED
    session.commit()
    return tx


class TestSchemaShape:
    def test_voided_columns_exist(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        cols = {c["name"]: c for c in inspect(eng).get_columns("transactions")}
        assert "voided_by_transaction_id" in cols
        assert cols["voided_by_transaction_id"]["nullable"] is True
        assert "voided_at" in cols
        assert cols["voided_at"]["nullable"] is True
        eng.dispose()

    def test_voided_by_self_fk_exists(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        fks = inspect(eng).get_foreign_keys("transactions")
        self_fks = [
            fk
            for fk in fks
            if "voided_by_transaction_id" in fk["constrained_columns"]
            and fk["referred_table"] == "transactions"
        ]
        assert self_fks, f"expected self-FK on voided_by_transaction_id, got {fks}"
        eng.dispose()

    def test_round_trip_upgrade_downgrade(self, tmp_path):
        # Pin to the revision before P5.0 so this test stays meaningful as
        # later phases pile on. Pre-P5.0 head was b8a91c2f3d44 (P4.3.a).
        db_path = tmp_path / "tulip.db"
        cfg = _make_alembic_cfg(f"sqlite:///{db_path}")
        upgrade(cfg, "head")
        downgrade(cfg, "b8a91c2f3d44")
        from sqlalchemy import create_engine

        eng = create_engine(f"sqlite:///{db_path}")
        cols = {c["name"] for c in inspect(eng).get_columns("transactions")}
        assert "voided_by_transaction_id" not in cols
        assert "voided_at" not in cols
        eng.dispose()


class TestVoidLinkPersistence:
    def test_set_voided_by_persists_round_trip(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            tx = _seed_balanced_tx(s, h.id)
            reversal = _seed_balanced_tx(s, h.id)
            tx.voided_by_transaction_id = reversal.id
            from datetime import datetime

            tx.voided_at = datetime.now(UTC)
            s.commit()

            with Smaker() as s2:
                fresh = s2.get(Transaction, (h.id, tx.id))
                assert fresh is not None
                assert fresh.voided_by_transaction_id == reversal.id
                assert fresh.voided_at is not None

    def test_orphan_voided_by_id_rejected(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            tx = _seed_balanced_tx(s, h.id)
            tx.voided_by_transaction_id = uuid4()  # no matching tx
            with pytest.raises(IntegrityError):
                s.commit()
