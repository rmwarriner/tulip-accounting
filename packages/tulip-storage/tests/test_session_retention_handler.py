"""Tests for ``run_session_retention`` (#344).

The handler prunes ``sessions`` (revoked) and ``mfa_recovery_codes``
(used) past the retention window (default 90d, overridden via
``households.audit_retention_policy.session_retention_days``).

Active sessions (``revoked_at IS NULL``) and unused codes
(``used_at IS NULL``) MUST NEVER be touched — they're load-bearing for
ongoing auth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.audit_log_helpers import _TRIGGER_NO_DELETE_SQL, _TRIGGER_NO_UPDATE_SQL
from tulip_storage.migrations._triggers import INITIAL_TRIGGERS, P4_0_SHADOW_TRIGGERS
from tulip_storage.models import AuditLog, Base, Household, MfaRecoveryCode
from tulip_storage.models import Session as SessionRow
from tulip_storage.runner.handlers.session_retention import (
    _DEFAULT_SESSION_RETENTION_DAYS,
    run_session_retention,
)


@pytest.fixture
def engine() -> Engine:
    eng = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _r):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        for ddl in INITIAL_TRIGGERS:
            conn.execute(text(ddl))
        for ddl in P4_0_SHADOW_TRIGGERS:
            conn.execute(text(ddl))
        # #333: install audit_log immutability triggers (the
        # session_retention handler uses the carve-out helper).
        conn.execute(text(_TRIGGER_NO_UPDATE_SQL))
        conn.execute(text(_TRIGGER_NO_DELETE_SQL))
    yield eng
    eng.dispose()


@pytest.fixture
def session_maker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def household(session_maker: sessionmaker[Session]) -> Household:
    with session_maker() as s:
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        s.add(h)
        s.commit()
        s.refresh(h)
        return h


def _add_user(session_maker, household_id):
    """Add a user the sessions FK can reference."""
    from tulip_storage.models import User, UserRole

    user_id = uuid4()
    with session_maker() as s:
        s.add(
            User(
                household_id=household_id,
                id=user_id,
                email=f"u-{user_id}@example.com",
                password_hash="x",
                display_name="U",
                role=UserRole.ADMIN,
            )
        )
        s.commit()
    return user_id


def _add_session(session_maker, *, household_id, user_id, revoked_at=None):
    with session_maker() as s:
        s.add(
            SessionRow(
                household_id=household_id,
                id=uuid4(),
                user_id=user_id,
                refresh_token_hash=f"hash-{uuid4().hex}",
                expires_at=datetime.now(tz=UTC) + timedelta(days=30),
                revoked_at=revoked_at,
            )
        )
        s.commit()


def _add_recovery_code(session_maker, *, household_id, user_id, used_at=None):
    with session_maker() as s:
        s.add(
            MfaRecoveryCode(
                household_id=household_id,
                id=uuid4(),
                user_id=user_id,
                code_hash=f"hash-{uuid4().hex}",
                used_at=used_at,
            )
        )
        s.commit()


class TestSessionRetentionHandler:
    def test_revoked_session_past_retention_is_deleted(self, session_maker, household):
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        old = now - timedelta(days=_DEFAULT_SESSION_RETENTION_DAYS + 1)
        user_id = _add_user(session_maker, household.id)
        _add_session(session_maker, household_id=household.id, user_id=user_id, revoked_at=old)

        summary = run_session_retention(session_maker, now=now)
        assert summary[household.id]["sessions_deleted"] == 1
        with session_maker() as s:
            assert s.execute(select(SessionRow)).scalars().all() == []

    def test_recently_revoked_session_is_preserved(self, session_maker, household):
        """Just inside the window — must NOT be deleted."""
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        recent = now - timedelta(days=_DEFAULT_SESSION_RETENTION_DAYS - 1)
        user_id = _add_user(session_maker, household.id)
        _add_session(session_maker, household_id=household.id, user_id=user_id, revoked_at=recent)

        summary = run_session_retention(session_maker, now=now)
        assert summary[household.id]["sessions_deleted"] == 0

    def test_active_session_is_never_touched(self, session_maker, household):
        """``revoked_at IS NULL`` means active — must NEVER be deleted."""
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        user_id = _add_user(session_maker, household.id)
        _add_session(session_maker, household_id=household.id, user_id=user_id, revoked_at=None)

        summary = run_session_retention(session_maker, now=now)
        assert summary[household.id]["sessions_deleted"] == 0
        with session_maker() as s:
            assert len(s.execute(select(SessionRow)).scalars().all()) == 1

    def test_used_recovery_code_past_retention_is_deleted(self, session_maker, household):
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        old = now - timedelta(days=_DEFAULT_SESSION_RETENTION_DAYS + 1)
        user_id = _add_user(session_maker, household.id)
        _add_recovery_code(session_maker, household_id=household.id, user_id=user_id, used_at=old)

        summary = run_session_retention(session_maker, now=now)
        assert summary[household.id]["recovery_codes_deleted"] == 1

    def test_unused_recovery_code_is_never_touched(self, session_maker, household):
        """``used_at IS NULL`` means the code is a bookmark for future
        login — must NEVER be deleted.
        """
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        user_id = _add_user(session_maker, household.id)
        _add_recovery_code(session_maker, household_id=household.id, user_id=user_id, used_at=None)

        summary = run_session_retention(session_maker, now=now)
        assert summary[household.id]["recovery_codes_deleted"] == 0

    def test_summary_audit_row_written_when_any_row_deleted(self, session_maker, household):
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        old = now - timedelta(days=_DEFAULT_SESSION_RETENTION_DAYS + 1)
        user_id = _add_user(session_maker, household.id)
        _add_session(session_maker, household_id=household.id, user_id=user_id, revoked_at=old)

        run_session_retention(session_maker, now=now)

        with session_maker() as s:
            rows = [
                r
                for r in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
                if r.action == "session.pruned"
            ]
            assert len(rows) == 1
            assert rows[0].metadata_["sessions_deleted"] == 1
            assert rows[0].metadata_["recovery_codes_deleted"] == 0

    def test_no_audit_row_when_nothing_deleted(self, session_maker, household):
        """Empty household → no audit row (the per-household summary is opt-in on count > 0)."""
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        run_session_retention(session_maker, now=now)

        with session_maker() as s:
            rows = [
                r for r in s.execute(select(AuditLog)).scalars() if r.action == "session.pruned"
            ]
            assert rows == []

    def test_operator_override_via_audit_retention_policy_key(self, session_maker, household):
        """``session_retention_days`` in audit_retention_policy overrides the default."""
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        # 10 days past the default 90 → would normally be deleted.
        edge = now - timedelta(days=_DEFAULT_SESSION_RETENTION_DAYS + 10)
        user_id = _add_user(session_maker, household.id)
        _add_session(session_maker, household_id=household.id, user_id=user_id, revoked_at=edge)

        # Override retention to 365 days — the row should now survive.
        with session_maker() as s:
            h = s.get(Household, household.id)
            h.audit_retention_policy = {"session_retention_days": 365}
            s.commit()

        summary = run_session_retention(session_maker, now=now)
        assert summary[household.id]["sessions_deleted"] == 0
