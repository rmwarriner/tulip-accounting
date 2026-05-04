"""Tests for the P5.1 imports + reconciliations migration.

Adds 7 new tables (``attachments``, ``attachment_links``, ``import_batches``,
``statement_lines``, ``reconciliations``, ``reconciliation_matches``,
``csv_profiles``) and 5 new nullable columns on ``transactions``
(``cleared_at``, ``reconciled_at``, ``reconciliation_id``,
``imported_from_id``, ``carried_forward_from_reconciliation_id``).
Mirrors the structure of test_p40_migration.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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


def _seed_account(session: Session, household_id) -> Account:
    a = Account(
        household_id=household_id,
        id=uuid4(),
        code="1110",
        name="Checking",
        type=AccountType.ASSET,
        currency="USD",
        visibility="shared",
    )
    session.add(a)
    session.commit()
    return a


def _seed_posted_tx(session: Session, household_id) -> Transaction:
    cash = _seed_account(session, household_id)
    food = Account(
        household_id=household_id,
        id=uuid4(),
        code="5100",
        name="Food",
        type=AccountType.EXPENSE,
        currency="USD",
        visibility="shared",
    )
    session.add(food)
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
    def test_new_tables_exist(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        names = set(inspect(eng).get_table_names())
        assert {
            "attachments",
            "attachment_links",
            "import_batches",
            "statement_lines",
            "reconciliations",
            "reconciliation_matches",
            "csv_profiles",
        } <= names
        eng.dispose()

    def test_transactions_gains_five_nullable_columns(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        cols = {c["name"]: c for c in inspect(eng).get_columns("transactions")}
        for name in (
            "cleared_at",
            "reconciled_at",
            "reconciliation_id",
            "imported_from_id",
            "carried_forward_from_reconciliation_id",
        ):
            assert name in cols, f"missing column {name}"
            assert cols[name]["nullable"] is True
        eng.dispose()

    def test_transactions_void_fk_still_intact(self, migrated_db):
        # P5.0's self-FK on voided_by_transaction_id must survive the
        # batch_alter_table rebuild that adds the P5.1 columns.
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
        assert self_fks, f"P5.0 self-FK lost; got {fks}"
        eng.dispose()

    def test_transactions_p51_fks_present(self, migrated_db):
        db_url, _ = migrated_db
        from sqlalchemy import create_engine

        eng = create_engine(db_url)
        fks = inspect(eng).get_foreign_keys("transactions")
        targets_by_col: dict[str, str] = {}
        for fk in fks:
            for col in fk["constrained_columns"]:
                targets_by_col[col] = fk["referred_table"]
        assert targets_by_col.get("reconciliation_id") == "reconciliations"
        assert targets_by_col.get("imported_from_id") == "import_batches"
        assert targets_by_col.get("carried_forward_from_reconciliation_id") == "reconciliations"
        eng.dispose()

    def test_round_trip_upgrade_downgrade(self, tmp_path):
        db_path = tmp_path / "tulip.db"
        cfg = _make_alembic_cfg(f"sqlite:///{db_path}")
        upgrade(cfg, "head")
        downgrade(cfg, "-1")
        from sqlalchemy import create_engine

        eng = create_engine(f"sqlite:///{db_path}")
        names = set(inspect(eng).get_table_names())
        for t in (
            "attachments",
            "attachment_links",
            "import_batches",
            "statement_lines",
            "reconciliations",
            "reconciliation_matches",
            "csv_profiles",
        ):
            assert t not in names
        cols = {c["name"] for c in inspect(eng).get_columns("transactions")}
        for c in (
            "cleared_at",
            "reconciled_at",
            "reconciliation_id",
            "imported_from_id",
            "carried_forward_from_reconciliation_id",
        ):
            assert c not in cols
        eng.dispose()


class TestForeignKeys:
    def test_orphan_reconciliation_id_on_transactions_rejected(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            tx = _seed_posted_tx(s, h.id)
            tx.reconciliation_id = uuid4()  # no matching reconciliation
            with pytest.raises(IntegrityError):
                s.commit()

    def test_orphan_imported_from_id_on_transactions_rejected(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            tx = _seed_posted_tx(s, h.id)
            tx.imported_from_id = uuid4()  # no matching import_batch
            with pytest.raises(IntegrityError):
                s.commit()


class TestBalanceTriggerStillFires:
    def test_unbalanced_post_rejects_after_p51(self, migrated_db):
        # P5.1's batch_alter_table on transactions drops + recreates
        # INITIAL_TRIGGERS. Confirm balance enforcement survives.
        _, Smaker = migrated_db
        with Smaker() as s:
            h = _seed_household(s)
            cash = _seed_account(s, h.id)
            food = Account(
                household_id=h.id,
                id=uuid4(),
                code="5100",
                name="Food",
                type=AccountType.EXPENSE,
                currency="USD",
                visibility="shared",
            )
            s.add(food)
            s.commit()
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
                        amount=Decimal("10.00"),
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


class TestAttachmentDedupIndex:
    def test_unique_content_hash_per_household(self, migrated_db):
        # ADR-0004 §Q6: same file imported twice must dedup on
        # (household_id, content_hash).
        _, Smaker = migrated_db
        with Smaker() as s:
            from tulip_storage.models import Attachment

            h = _seed_household(s)
            now = datetime.now(tz=UTC)
            s.add(
                Attachment(
                    household_id=h.id,
                    id=uuid4(),
                    filename="may.ofx",
                    content_type="application/x-ofx",
                    size_bytes=100,
                    content_hash="abc123",
                    storage_uri="fs://abc123",
                    uploaded_at=now,
                )
            )
            s.commit()
            s.add(
                Attachment(
                    household_id=h.id,
                    id=uuid4(),
                    filename="may-renamed.ofx",
                    content_type="application/x-ofx",
                    size_bytes=100,
                    content_hash="abc123",  # same hash
                    storage_uri="fs://abc123",
                    uploaded_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                s.commit()


class TestReconciliationMatchCascades:
    def test_delete_reconciliation_cascades_matches(self, migrated_db):
        _, Smaker = migrated_db
        with Smaker() as s:
            from tulip_storage.models import (
                Reconciliation,
                ReconciliationMatch,
                ReconciliationStatus,
                StatementLine,
            )

            h = _seed_household(s)
            cash = _seed_account(s, h.id)
            tx = _seed_posted_tx(s, h.id)
            now = datetime.now(tz=UTC)

            recon = Reconciliation(
                household_id=h.id,
                id=uuid4(),
                account_id=cash.id,
                statement_period_start=date(2026, 5, 1),
                statement_period_end=date(2026, 5, 31),
                statement_starting_balance=Decimal("0"),
                statement_ending_balance=Decimal("-12.50"),
                currency="USD",
                status=ReconciliationStatus.IN_PROGRESS,
                created_at=now,
            )
            s.add(recon)
            s.flush()

            # Need an import_batch + statement_line first.
            from tulip_storage.models import (
                Attachment,
                ImportBatch,
                ImportBatchStatus,
                SourceFormat,
            )

            att = Attachment(
                household_id=h.id,
                id=uuid4(),
                filename="may.ofx",
                content_type="application/x-ofx",
                size_bytes=10,
                content_hash="hash",
                storage_uri="fs://hash",
                uploaded_at=now,
            )
            s.add(att)
            s.flush()
            batch = ImportBatch(
                household_id=h.id,
                id=uuid4(),
                account_id=cash.id,
                source_format=SourceFormat.OFX,
                source_filename="may.ofx",
                source_file_attachment_id=att.id,
                status=ImportBatchStatus.PARSED,
                created_at=now,
            )
            s.add(batch)
            s.flush()
            line = StatementLine(
                household_id=h.id,
                id=uuid4(),
                import_batch_id=batch.id,
                line_number=1,
                posted_date=date(2026, 5, 12),
                amount=Decimal("-12.50"),
                currency="USD",
                description="LUNCH",
                raw_json="{}",
            )
            s.add(line)
            s.flush()
            match = ReconciliationMatch(
                household_id=h.id,
                id=uuid4(),
                reconciliation_id=recon.id,
                statement_line_id=line.id,
                ledger_transaction_id=tx.id,
                match_amount=Decimal("12.50"),
                currency="USD",
                created_at=now,
            )
            s.add(match)
            s.commit()
            match_id = match.id

            # Delete the reconciliation.
            s.delete(recon)
            s.commit()

            # Match should cascade-delete.
            from sqlalchemy import select

            remaining = s.execute(
                select(ReconciliationMatch).where(ReconciliationMatch.id == match_id)
            ).scalar_one_or_none()
            assert remaining is None
