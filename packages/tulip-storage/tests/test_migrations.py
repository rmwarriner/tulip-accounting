"""Tests for Alembic migrations and the balanced-postings trigger."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import event, inspect, text
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


class TestEncryptionV1Wrap:
    """#338 (audit M-6): pre-#338 v1 blobs get a ``0x01`` prefix at upgrade."""

    def _build_pre338_blob(self, master_key: bytes, plaintext: bytes) -> bytes:
        """Mint a raw v1 blob (no version prefix) as if pre-#338 had written it."""
        import os as _os

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = _os.urandom(12)
        ct = AESGCM(master_key).encrypt(nonce, plaintext, associated_data=None)
        return nonce + ct

    def test_legacy_blob_gets_v1_prefix(self, tmp_path):
        """Migrate forward with a pre-existing legacy blob — assert it's wrapped."""
        from sqlalchemy import create_engine
        from sqlalchemy import text as _text

        from tulip_storage.encryption import decrypt_field

        db_path = tmp_path / "wrap.db"
        cfg = _make_alembic_cfg(f"sqlite:///{db_path}")
        master_key = b"\x42" * 32

        # Step to the revision BEFORE the wrap migration so we can plant
        # a raw v1 blob in the legacy shape, then forward-migrate.
        upgrade(cfg, "a6f1c9b3d8e4")

        eng = create_engine(f"sqlite:///{db_path}", future=True)
        hid = uuid4()
        uid = uuid4()
        try:
            with eng.begin() as conn:
                conn.execute(
                    _text(
                        "INSERT INTO households (id, name, base_currency) VALUES (:id, :name, :cur)"
                    ),
                    {"id": str(hid), "name": "T", "cur": "USD"},
                )
                conn.execute(
                    _text(
                        "INSERT INTO users (household_id, id, email, password_hash, "
                        "display_name, role, totp_secret_encrypted) VALUES "
                        "(:h, :i, :e, :p, :n, 'admin', :blob)"
                    ),
                    {
                        "h": str(hid),
                        "i": str(uid),
                        "e": "a@b.c",
                        "p": "$argon2i$x",
                        "n": "T",
                        "blob": self._build_pre338_blob(master_key, b"legacy-totp"),
                    },
                )
        finally:
            eng.dispose()

        # Forward-migrate.
        upgrade(cfg, "e9c4f1b7d2a5")

        # The blob now starts with 0x01 and decrypts via the v1 path
        # (AAD is ignored on v1).
        eng = create_engine(f"sqlite:///{db_path}", future=True)
        try:
            with eng.connect() as conn:
                blob = conn.execute(
                    _text("SELECT totp_secret_encrypted FROM users WHERE id = :i"),
                    {"i": str(uid)},
                ).scalar_one()
            assert blob[0] == 0x01
            assert decrypt_field(bytes(blob), master_key, aad=b"any-aad") == b"legacy-totp"
        finally:
            eng.dispose()

        # Downgrade strips the 0x01 prefix back to the legacy raw shape.
        downgrade(cfg, "a6f1c9b3d8e4")
        eng = create_engine(f"sqlite:///{db_path}", future=True)
        try:
            with eng.connect() as conn:
                blob_after = conn.execute(
                    _text("SELECT totp_secret_encrypted FROM users WHERE id = :i"),
                    {"i": str(uid)},
                ).scalar_one()
            assert blob_after == bytes(blob[1:])
        finally:
            eng.dispose()

    def test_migration_is_idempotent(self, tmp_path):
        """Re-running the wrap migration after it's done is a no-op."""
        from tulip_storage.encryption import wrap_legacy_v1_blob

        db_path = tmp_path / "wrap-idem.db"
        cfg = _make_alembic_cfg(f"sqlite:///{db_path}")
        upgrade(cfg, "e9c4f1b7d2a5")
        upgrade(cfg, "e9c4f1b7d2a5")  # second time — no error.
        # Sanity: helper directly.
        ct = b"\x01" + b"\xaa" * (12 + 16 + 5)
        assert wrap_legacy_v1_blob(ct) == ct


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


class TestDeprecatedViewerRole:
    """#341: ``UserRole.VIEWER`` is removed from the enum after the migration
    runs. Defensive UPDATE in the migration demotes any pre-existing VIEWER
    rows to MEMBER (audit M-26).

    The DB column carries no CHECK constraint by design — the existing model
    uses ``native_enum=False`` without ``create_constraint=True``. The Python
    enum class is the enforcement boundary; any row written via raw SQL with
    a value outside the enum fails to re-hydrate through the ORM.
    """

    def test_userrole_enum_does_not_contain_viewer(self):
        from tulip_storage.models import UserRole

        assert {r.name for r in UserRole} == {"ADMIN", "MEMBER"}
        assert not hasattr(UserRole, "VIEWER")

    def test_reading_raw_viewer_row_fails_via_orm(self, migrated_db):

        from tulip_storage.models import User

        _, Smaker = migrated_db
        h_id = uuid4()
        u_id = uuid4()
        with Smaker() as s:
            h = Household(id=h_id, name="H", base_currency="USD")
            s.add(h)
            s.flush()
            s.execute(
                text(
                    "INSERT INTO users "
                    "(household_id, id, email, password_hash, display_name, role, "
                    "created_at, updated_at) "
                    "VALUES (:h, :u, 'v@example.com', 'x', 'V', 'VIEWER', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                ),
                {"h": str(h_id), "u": str(u_id)},
            )
            s.commit()

        with Smaker() as s, pytest.raises(LookupError):
            s.get(User, (h_id, u_id))

    def test_upgrade_demotes_existing_viewer_rows_to_member(self, tmp_path):
        """downgrade → insert VIEWER → upgrade demotes the row to MEMBER."""
        from sqlalchemy import create_engine

        db_path = tmp_path / "tulip.db"
        db_url = f"sqlite:///{db_path}"
        cfg = _make_alembic_cfg(db_url)
        # Go to the revision *just before* the deprecation lands so VIEWER
        # is still in the CHECK constraint.
        upgrade(cfg, "b4c8e2d9a1f5")

        eng = create_engine(db_url, future=True)
        try:
            household_id = uuid4()
            user_id = uuid4()
            ts = "2026-01-01 00:00:00"
            with eng.begin() as c:
                c.execute(
                    text(
                        "INSERT INTO households "
                        "(id, name, base_currency, created_at, updated_at, "
                        "ai_policy, audit_retention_policy) "
                        "VALUES (:i, 'H', 'USD', :ts, :ts, '{}', '{}')"
                    ),
                    {"i": str(household_id), "ts": ts},
                )
                c.execute(
                    text(
                        "INSERT INTO users "
                        "(household_id, id, email, password_hash, display_name, "
                        "role, created_at, updated_at) "
                        "VALUES (:h, :u, 'v@example.com', 'x', 'V', "
                        "'VIEWER', :ts, :ts)"
                    ),
                    {"h": str(household_id), "u": str(user_id), "ts": ts},
                )

            upgrade(cfg, "head")

            with eng.connect() as c:
                row = c.execute(
                    text("SELECT role FROM users WHERE id = :u"),
                    {"u": str(user_id)},
                ).first()
                assert row is not None
                assert row[0] == "MEMBER"
        finally:
            eng.dispose()


class TestAuditLogImmutabilityTrigger:
    """#333 / security audit M-22: SQLite triggers reject UPDATE / DELETE
    on ``audit_log`` to defend against application-layer regressions and
    accidental ``sqlite3`` shell DELETEs. The household-erasure cascade
    is carved out via a connection-scoped temp marker.
    """

    def _seed_audit_row(self, session: Session) -> tuple[Household, object]:
        """Insert one household + one audit_log row; return both."""
        from datetime import UTC, datetime

        from tulip_storage.models import AuditLog as _AuditLog
        from tulip_storage.repositories.audit_log import AuditLogWriter

        h = Household(id=uuid4(), name="Audit", base_currency="USD")
        session.add(h)
        session.flush()
        row = AuditLogWriter(session, h.id).write(
            action="test",
            actor_kind="user",
            actor_user_id=None,
            entity_type="household",
            entity_id=h.id,
        )
        session.commit()
        _ = datetime.now(tz=UTC), _AuditLog  # silence unused imports
        return h, row

    def test_direct_update_is_rejected(self, migrated_db):

        _, Smaker = migrated_db
        with Smaker() as s:
            self._seed_audit_row(s)
        with Smaker() as s, pytest.raises((IntegrityError, OperationalError), match="append-only"):
            s.execute(text("UPDATE audit_log SET action = 'tampered'"))
            s.commit()

    def test_direct_delete_is_rejected(self, migrated_db):

        _, Smaker = migrated_db
        with Smaker() as s:
            self._seed_audit_row(s)
        with Smaker() as s, pytest.raises((IntegrityError, OperationalError), match="append-only"):
            s.execute(text("DELETE FROM audit_log"))
            s.commit()

    def test_household_cascade_delete_succeeds_inside_carveout_helper(self, migrated_db):
        """The right-to-erasure flow brackets its ``DELETE FROM households``
        with the ``audit_log_deletion_allowed`` context manager; the
        cascade then clears audit_log rows. After the block exits the
        trigger is reinstated.
        """
        from sqlalchemy import delete as sa_delete

        from tulip_storage.audit_log_helpers import audit_log_deletion_allowed

        _, Smaker = migrated_db
        with Smaker() as s:
            h, _ = self._seed_audit_row(s)
            household_id = h.id

        with Smaker() as s, audit_log_deletion_allowed(s):
            s.execute(sa_delete(Household).where(Household.id == household_id))
            s.commit()

        with Smaker() as s:
            remaining = s.execute(
                text("SELECT COUNT(*) FROM audit_log WHERE household_id = :h"),
                {"h": str(household_id)},
            ).scalar_one()
            assert remaining == 0

        # After the context exits, the trigger is reinstated — a direct
        # delete attempt still fails.
        with Smaker() as s:
            h2, _ = self._seed_audit_row(s)
        with Smaker() as s, pytest.raises((IntegrityError, OperationalError), match="append-only"):
            s.execute(text("DELETE FROM audit_log"))
            s.commit()
        # Silence the unused-var warning — h2 created intentionally.
        _ = h2
