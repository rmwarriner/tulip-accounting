"""Tests for the P5.4.a apply/promote migration.

Adds one nullable column on ``statement_lines``:
``promoted_transaction_id`` — composite FK to ``transactions
(household_id, id)``. Used by the promote endpoint to track which
ledger transaction a parsed line was promoted into, for O(1)
idempotency lookup. Mirrors the round-trip + orphan-FK pattern of
``test_p51_migration.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.models import (
    Account,
    AccountType,
    Household,
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

    eng = create_engine(db_url, future=True)

    @event.listens_for(eng, "connect")
    def _enable_fk(dc, _r):  # type: ignore[no-untyped-def]
        cur = dc.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    try:
        yield db_url, sessionmaker(eng, expire_on_commit=False)
    finally:
        eng.dispose()


def _seed_household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _seed_account(session: Session, household_id) -> Account:
    a = Account(
        household_id=household_id,
        id=uuid4(),
        code="1110",
        name="Checking",
        type=AccountType.ASSET,
        currency="USD",
        is_active=True,
    )
    session.add(a)
    session.commit()
    return a


class TestSchemaShape:
    def test_promoted_transaction_id_column_exists(self, migrated_db):
        _, sm = migrated_db
        with sm() as s:
            cols = {c["name"]: c for c in inspect(s.get_bind()).get_columns("statement_lines")}
            assert "promoted_transaction_id" in cols
            assert cols["promoted_transaction_id"]["nullable"] is True

    def test_promoted_transaction_id_fk_to_transactions(self, migrated_db):
        _, sm = migrated_db
        with sm() as s:
            fks = inspect(s.get_bind()).get_foreign_keys("statement_lines")
            promote_fk = next(
                (fk for fk in fks if "promoted_transaction_id" in fk["constrained_columns"]),
                None,
            )
            assert promote_fk is not None, "FK on promoted_transaction_id missing"
            assert promote_fk["referred_table"] == "transactions"
            assert sorted(promote_fk["constrained_columns"]) == [
                "household_id",
                "promoted_transaction_id",
            ]
            assert sorted(promote_fk["referred_columns"]) == ["household_id", "id"]

    def test_round_trip_upgrade_downgrade(self, tmp_path):
        db_path = tmp_path / "rt.db"
        db_url = f"sqlite:///{db_path}"
        cfg = _make_alembic_cfg(db_url)
        upgrade(cfg, "head")
        # Down to P5.1, then back up.
        downgrade(cfg, "f4a6b9c2e7d3")
        eng = create_engine(db_url, future=True)
        try:
            cols = {c["name"] for c in inspect(eng).get_columns("statement_lines")}
            assert "promoted_transaction_id" not in cols
        finally:
            eng.dispose()
        upgrade(cfg, "head")
        eng2 = create_engine(db_url, future=True)
        try:
            cols2 = {c["name"] for c in inspect(eng2).get_columns("statement_lines")}
            assert "promoted_transaction_id" in cols2
        finally:
            eng2.dispose()


class TestForeignKey:
    def test_orphan_promoted_transaction_id_rejected(self, migrated_db):
        """Inserting a statement_line with a non-existent promoted_transaction_id is rejected."""
        _, sm = migrated_db
        with sm() as s:
            h = _seed_household(s)
            a = _seed_account(s, h.id)
            # Create import_batch + attachment minimally via raw SQL — model wiring
            # is out of scope for the migration test. We just need an FK target.
            attachment_id = uuid4()
            s.execute(
                text(
                    "INSERT INTO attachments (household_id, id, filename, "
                    "content_type, size_bytes, content_hash, storage_uri, "
                    "uploaded_at) VALUES "
                    "(:hid, :id, 'x.ofx', :ct, 1, :hash, 's3://x', :now)"
                ),
                {
                    "hid": str(h.id),
                    "id": str(attachment_id),
                    "hash": "x" * 64,
                    "ct": "application/x-ofx",
                    "now": datetime.now(UTC).isoformat(),
                },
            )
            batch_id = uuid4()
            s.execute(
                text(
                    "INSERT INTO import_batches (household_id, id, account_id, "
                    "source_format, source_filename, source_file_attachment_id, "
                    "status, imported_count, skipped_count, error_count, "
                    "summary_json, created_by_user_id, created_at) "
                    "VALUES (:hid, :id, :aid, 'ofx', 'x.ofx', :att, 'parsed', "
                    "0, 0, 0, '{}', :uid, :now)"
                ),
                {
                    "hid": str(h.id),
                    "id": str(batch_id),
                    "aid": str(a.id),
                    "att": str(attachment_id),
                    "uid": str(uuid4()),
                    "now": datetime.now(UTC).isoformat(),
                },
            )
            s.commit()

            with pytest.raises(IntegrityError):
                s.execute(
                    text(
                        "INSERT INTO statement_lines (household_id, id, "
                        "import_batch_id, line_number, posted_date, amount, "
                        "currency, description, raw_json, is_excluded, "
                        "promoted_transaction_id) VALUES "
                        "(:hid, :id, :bid, 1, :d, :amt, 'USD', 'x', '{}', 0, :ptx)"
                    ),
                    {
                        "hid": str(h.id),
                        "id": str(uuid4()),
                        "bid": str(batch_id),
                        "d": date(2026, 5, 6).isoformat(),
                        "amt": "-10.00",
                        "ptx": str(uuid4()),  # nonexistent
                    },
                )
                s.commit()
