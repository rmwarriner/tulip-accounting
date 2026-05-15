"""Tests for ``run_audit_retention`` (#245).

The handler prunes ``audit_log`` rows past their per-tier TTL. Tier is
dispatched by ``audit_log.action`` via a static map; defaults come from
``_TIER_DEFAULTS`` and are overridden per-household via
``households.audit_retention_policy``.

These tests seed rows of known ages + actions and assert the right ones
disappear. The handler also writes an ``audit.pruned`` summary row when
anything got deleted; we assert that too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.migrations._triggers import INITIAL_TRIGGERS, P4_0_SHADOW_TRIGGERS
from tulip_storage.models import AuditLog, Base, Household
from tulip_storage.runner.handlers.audit_retention import (
    _RETENTION_TIER_BY_ACTION,
    _TIER_DEFAULTS,
    run_audit_retention,
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


def _add_audit(
    session_maker: sessionmaker[Session],
    *,
    household_id,
    action: str,
    occurred_at: datetime,
) -> None:
    with session_maker() as s:
        s.add(
            AuditLog(
                id=uuid4(),
                household_id=household_id,
                occurred_at=occurred_at,
                actor_kind="user",
                action=action,
                entity_type="user",
                entity_id=household_id,
            )
        )
        s.commit()


class TestRunAuditRetention:
    def test_old_ledger_row_kept_within_7_year_default(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """A ledger-tier row 6 years old is inside the 7-year default → kept."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        _add_audit(
            session_maker,
            household_id=household.id,
            action="create",  # ledger_days tier
            occurred_at=now - timedelta(days=365 * 6),
        )

        run_audit_retention(session_maker, now=now)

        with session_maker() as s:
            rows = list(s.execute(select(AuditLog)).scalars().all())
        # The seeded ledger row survived; no summary row written (no deletions).
        assert len(rows) == 1
        assert rows[0].action == "create"

    def test_very_old_ledger_row_pruned_past_7_years(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """A ledger-tier row 8 years old is past the 7-year default → deleted."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        _add_audit(
            session_maker,
            household_id=household.id,
            action="create",
            occurred_at=now - timedelta(days=365 * 8),
        )

        summary = run_audit_retention(session_maker, now=now)

        with session_maker() as s:
            rows = list(s.execute(select(AuditLog)).scalars().all())
        # The seeded row was deleted; a summary row was written.
        actions = sorted(r.action for r in rows)
        assert actions == ["audit.pruned"]
        # The summary row records the per-tier count.
        summary_row = rows[0]
        assert summary_row.metadata_["deleted_per_tier"]["ledger_days"] == 1
        assert summary[household.id]["ledger_days"] == 1

    def test_auth_row_pruned_past_90_day_default(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """``login_failed`` is auth_days tier (90d); 100-day-old row gets cut."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        _add_audit(
            session_maker,
            household_id=household.id,
            action="login_failed",
            occurred_at=now - timedelta(days=100),
        )
        _add_audit(
            session_maker,
            household_id=household.id,
            action="login_failed",
            occurred_at=now - timedelta(days=10),  # well within 90d
        )

        run_audit_retention(session_maker, now=now)

        with session_maker() as s:
            rows = list(s.execute(select(AuditLog)).scalars().all())
        # One login_failed survives, one was deleted, one summary row written.
        survivors = [r for r in rows if r.action == "login_failed"]
        assert len(survivors) == 1
        summary_rows = [r for r in rows if r.action == "audit.pruned"]
        assert len(summary_rows) == 1
        assert summary_rows[0].metadata_["deleted_per_tier"]["auth_days"] == 1

    def test_ai_consent_row_pruned_past_30_day_default(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """``ai.consent_changed`` is ai_days tier (30d); 45-day-old row gets cut."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        _add_audit(
            session_maker,
            household_id=household.id,
            action="ai.consent_changed",
            occurred_at=now - timedelta(days=45),
        )

        summary = run_audit_retention(session_maker, now=now)

        assert summary[household.id]["ai_days"] == 1

    def test_unmapped_action_uses_default_days_tier(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """An action string not in the map ages at default_days (90d safety net)."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        _add_audit(
            session_maker,
            household_id=household.id,
            action="completely_new_action_str",
            occurred_at=now - timedelta(days=100),
        )

        summary = run_audit_retention(session_maker, now=now)

        assert summary[household.id]["default_days"] == 1

    def test_policy_override_shortens_retention(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """Operator sets ledger_days=180; a 200-day-old ledger row is cut."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        with session_maker() as s:
            h = s.get(Household, household.id)
            assert h is not None
            h.audit_retention_policy = {"ledger_days": 180}
            s.commit()

        _add_audit(
            session_maker,
            household_id=household.id,
            action="create",
            occurred_at=now - timedelta(days=200),
        )

        summary = run_audit_retention(session_maker, now=now)

        assert summary[household.id]["ledger_days"] == 1

    def test_policy_typo_falls_through_to_default(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """An operator sets ledger_days to something non-positive → code default wins.

        Defensive: a typo (``"ledger_days": "1825"`` string, or ``0``, or
        ``-1``) must not disable the tier.
        """
        now = datetime(2026, 5, 15, tzinfo=UTC)
        with session_maker() as s:
            h = s.get(Household, household.id)
            assert h is not None
            h.audit_retention_policy = {"ledger_days": -5}
            s.commit()

        _add_audit(
            session_maker,
            household_id=household.id,
            action="create",
            occurred_at=now - timedelta(days=365 * 6),
        )

        summary = run_audit_retention(session_maker, now=now)

        # Default 7y wins; 6-year-old row survives.
        assert summary[household.id]["ledger_days"] == 0

    def test_no_deletions_writes_no_summary_row(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """If nothing got pruned, the handler is silent (no audit.pruned row)."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        _add_audit(
            session_maker,
            household_id=household.id,
            action="login",
            occurred_at=now - timedelta(days=5),
        )

        run_audit_retention(session_maker, now=now)

        with session_maker() as s:
            rows = list(s.execute(select(AuditLog)).scalars().all())
        assert [r.action for r in rows] == ["login"]

    def test_household_scoped_run_skips_other_households(
        self,
        session_maker: sessionmaker[Session],
        household: Household,
    ) -> None:
        """``household_id=`` argument limits the prune to one tenant."""
        now = datetime(2026, 5, 15, tzinfo=UTC)
        with session_maker() as s:
            other = Household(id=uuid4(), name="Other", base_currency="USD")
            s.add(other)
            s.commit()
            other_id = other.id

        _add_audit(
            session_maker,
            household_id=household.id,
            action="login_failed",
            occurred_at=now - timedelta(days=200),
        )
        _add_audit(
            session_maker,
            household_id=other_id,
            action="login_failed",
            occurred_at=now - timedelta(days=200),
        )

        # Run scoped to ``household`` only.
        summary = run_audit_retention(session_maker, now=now, household_id=household.id)

        assert list(summary.keys()) == [household.id]
        with session_maker() as s:
            rows = list(s.execute(select(AuditLog).order_by(AuditLog.action)).scalars().all())
        # household: prune fired (1 login_failed gone, 1 audit.pruned summary).
        # other: untouched (1 login_failed still there).
        actions_by_household: dict = {}
        for r in rows:
            actions_by_household.setdefault(r.household_id, []).append(r.action)
        assert actions_by_household[household.id] == ["audit.pruned"]
        assert actions_by_household[other_id] == ["login_failed"]


class TestTierMapCoverage:
    def test_every_default_has_a_positive_int(self) -> None:
        for key, days in _TIER_DEFAULTS.items():
            assert isinstance(days, int) and days > 0, f"{key} default {days!r} invalid"

    def test_every_tier_in_map_exists_in_defaults(self) -> None:
        for action, tier in _RETENTION_TIER_BY_ACTION.items():
            assert tier in _TIER_DEFAULTS, f"{action} maps to unknown tier {tier!r}"

    def test_summary_action_is_in_admin_tier(self) -> None:
        """The handler's own ``audit.pruned`` row must be tier-mapped or it
        ages at ``default_days``, which makes the trail vanish too fast.
        """
        assert _RETENTION_TIER_BY_ACTION.get("audit.pruned") == "admin_days"
