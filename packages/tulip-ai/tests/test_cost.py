"""Tests for ``tulip_ai.cost`` — pre-call cost-cap + rate-limit (P6.5.a)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from tulip_ai.audit import AIInvocationRecord, AIInvocationWriter, hash_prompt_payload
from tulip_ai.cost import (
    DEFAULT_RATE_LIMIT_PER_HOUR,
    PreCallApproval,
    PreCallBlock,
    check_cost_cap,
    check_rate_limit,
    enforce_pre_call,
)
from tulip_storage.models import AIInvocation, Household, User


def _seed_invocation(
    session: Session,
    *,
    household_id,
    user_id=None,
    cost: str = "0.01",
    outcome: str = "success",
    created_at: datetime | None = None,
) -> AIInvocation:
    """Write one ``ai_invocations`` row via the writer chokepoint."""
    record = AIInvocationRecord(
        household_id=household_id,
        actor_user_id=user_id,
        capability="categorize",
        policy_resolved="permissive",
        profile="default",
        outcome=outcome,
        prompt_hash=hash_prompt_payload({"x": 1}),
        provider="anthropic",
        model="claude-opus-4-7",
        cost_estimate_usd=Decimal(cost),
    )
    row = AIInvocationWriter(session).write(record)
    if created_at is not None:
        # Test override — `created_at` is server_default-stamped on insert.
        row.created_at = created_at
        session.flush()
    return row


class TestCheckCostCap:
    def test_no_cap_configured_always_allows(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, _ = household_and_user
        with session_maker() as s:
            decision = check_cost_cap(
                s,
                household_id=household.id,
                estimated_cost_usd=Decimal("99999.00"),
                monthly_cap_usd=None,
            )
        assert decision.kind == "allow"
        assert decision.cap_usd is None

    def test_under_cap_allows(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, _ = household_and_user
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, cost="3.00")
            s.commit()
            decision = check_cost_cap(
                s,
                household_id=household.id,
                estimated_cost_usd=Decimal("1.00"),
                monthly_cap_usd=Decimal("10.00"),
            )
        assert decision.kind == "allow"
        assert decision.spent_so_far_usd == Decimal("3.00")

    def test_at_cap_blocks(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, _ = household_and_user
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, cost="9.50")
            s.commit()
            decision = check_cost_cap(
                s,
                household_id=household.id,
                estimated_cost_usd=Decimal("1.00"),
                monthly_cap_usd=Decimal("10.00"),
            )
        assert decision.kind == "cap_exceeded"
        assert decision.spent_so_far_usd == Decimal("9.50")
        assert decision.cap_usd == Decimal("10.00")

    def test_only_billable_outcomes_count(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        """Cost-capped / policy-disabled rows never spent money."""
        household, _ = household_and_user
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, cost="50.00", outcome="cost_capped")
            _seed_invocation(s, household_id=household.id, cost="50.00", outcome="policy_disabled")
            _seed_invocation(s, household_id=household.id, cost="50.00", outcome="rate_limited")
            _seed_invocation(s, household_id=household.id, cost="2.00", outcome="success")
            s.commit()
            decision = check_cost_cap(
                s,
                household_id=household.id,
                estimated_cost_usd=Decimal("1.00"),
                monthly_cap_usd=Decimal("10.00"),
            )
        assert decision.kind == "allow"
        assert decision.spent_so_far_usd == Decimal("2.00")

    def test_previous_month_doesnt_count(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, _ = household_and_user
        now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
        last_month = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, cost="100.00", created_at=last_month)
            s.commit()
            decision = check_cost_cap(
                s,
                household_id=household.id,
                estimated_cost_usd=Decimal("1.00"),
                monthly_cap_usd=Decimal("10.00"),
                now=now,
            )
        assert decision.kind == "allow"
        assert decision.spent_so_far_usd == Decimal("0")


class TestCheckRateLimit:
    def test_under_limit_allows(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            for _ in range(3):
                _seed_invocation(s, household_id=household.id, user_id=user.id)
            s.commit()
            decision = check_rate_limit(
                s,
                household_id=household.id,
                user_id=user.id,
                limit_per_hour=10,
            )
        assert decision.kind == "allow"
        assert decision.count_in_window == 3
        assert decision.limit_per_hour == 10

    def test_at_limit_blocks(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            for _ in range(5):
                _seed_invocation(s, household_id=household.id, user_id=user.id)
            s.commit()
            decision = check_rate_limit(
                s,
                household_id=household.id,
                user_id=user.id,
                limit_per_hour=5,
            )
        assert decision.kind == "rate_limited"
        assert decision.count_in_window == 5

    def test_outside_window_doesnt_count(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
        long_ago = now - timedelta(hours=2)
        with session_maker() as s:
            for _ in range(10):
                _seed_invocation(s, household_id=household.id, user_id=user.id, created_at=long_ago)
            s.commit()
            decision = check_rate_limit(
                s,
                household_id=household.id,
                user_id=user.id,
                limit_per_hour=5,
                now=now,
            )
        assert decision.kind == "allow"
        assert decision.count_in_window == 0

    def test_other_users_dont_count(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user_a = household_and_user
        with session_maker() as s:
            user_b_id = uuid4()
            for _ in range(10):
                _seed_invocation(s, household_id=household.id, user_id=user_b_id)
            s.commit()
            decision = check_rate_limit(
                s,
                household_id=household.id,
                user_id=user_a.id,
                limit_per_hour=5,
            )
        assert decision.kind == "allow"
        assert decision.count_in_window == 0

    def test_default_limit_is_60(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            decision = check_rate_limit(s, household_id=household.id, user_id=user.id)
        assert decision.limit_per_hour == DEFAULT_RATE_LIMIT_PER_HOUR == 60

    def test_system_calls_user_id_none_bucket(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        """Per-user buckets stay independent: actor_user_id=NULL is its own pool."""
        household, user = household_and_user
        with session_maker() as s:
            for _ in range(5):
                _seed_invocation(s, household_id=household.id, user_id=user.id)
            for _ in range(2):
                _seed_invocation(s, household_id=household.id, user_id=None)
            s.commit()
            system = check_rate_limit(s, household_id=household.id, user_id=None, limit_per_hour=10)
            user_bucket = check_rate_limit(
                s, household_id=household.id, user_id=user.id, limit_per_hour=10
            )
        assert system.count_in_window == 2
        assert user_bucket.count_in_window == 5


class TestEnforcePreCall:
    """The combined gate every capability calls before adapter.chat()."""

    def _base_kwargs(self, household_id, user_id) -> dict:
        return {
            "household_id": household_id,
            "user_id": user_id,
            "rate_limit_per_hour": 60,
            "monthly_cost_cap_usd": None,
            "cost_cap_behaviour": "degrade",
            "fallback_provider": "ollama",
            "fallback_model": "llama3:70b",
            "primary_provider": "anthropic",
            "primary_model": "claude-opus-4-7",
        }

    def test_clean_allow_returns_primary_provider(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            result = enforce_pre_call(s, **self._base_kwargs(household.id, user.id))
        assert isinstance(result, PreCallApproval)
        assert result.provider == "anthropic"
        assert result.degraded is False

    def test_rate_limit_blocks_before_cost_check(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            for _ in range(60):
                _seed_invocation(s, household_id=household.id, user_id=user.id)
            s.commit()
            result = enforce_pre_call(s, **self._base_kwargs(household.id, user.id))
        assert isinstance(result, PreCallBlock)
        assert result.outcome == "rate_limited"

    def test_cost_cap_with_degrade_swaps_to_fallback(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, user_id=user.id, cost="10.00")
            s.commit()
            kwargs = self._base_kwargs(household.id, user.id)
            kwargs["monthly_cost_cap_usd"] = Decimal("5.00")
            result = enforce_pre_call(s, **kwargs)
        assert isinstance(result, PreCallApproval)
        assert result.provider == "ollama"
        assert result.degraded is True

    def test_cost_cap_with_hard_fail_blocks(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        household, user = household_and_user
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, user_id=user.id, cost="10.00")
            s.commit()
            kwargs = self._base_kwargs(household.id, user.id)
            kwargs["monthly_cost_cap_usd"] = Decimal("5.00")
            kwargs["cost_cap_behaviour"] = "hard_fail"
            result = enforce_pre_call(s, **kwargs)
        assert isinstance(result, PreCallBlock)
        assert result.outcome == "cost_capped"

    def test_cost_cap_degrade_without_fallback_provider_blocks(
        self,
        session_maker: sessionmaker[Session],
        household_and_user: tuple[Household, User],
    ) -> None:
        """ADR §Q7: ``degrade`` without a fallback provider behaves like ``hard_fail``."""
        household, user = household_and_user
        with session_maker() as s:
            _seed_invocation(s, household_id=household.id, user_id=user.id, cost="10.00")
            s.commit()
            kwargs = self._base_kwargs(household.id, user.id)
            kwargs["monthly_cost_cap_usd"] = Decimal("5.00")
            kwargs["fallback_provider"] = None
            kwargs["fallback_model"] = None
            result = enforce_pre_call(s, **kwargs)
        assert isinstance(result, PreCallBlock)
        assert result.outcome == "cost_capped"


# ---- M-23 (#334): atomic-gate lock ----------------------------------------


class TestHouseholdGateLockSerialization:
    """Concurrency tests for ``_acquire_household_gate_lock`` (#334)."""

    def _file_engine_and_household(self, db_path, *, busy_timeout_ms: int = 200):
        """Build a file-backed SQLite engine + seed one household.

        File-backed (not :memory:) so two separate connections see the
        same data; in-memory SQLite is per-connection and would defeat
        the concurrency test.
        """
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker

        from tulip_storage.migrations._triggers import (
            INITIAL_TRIGGERS,
            P4_0_SHADOW_TRIGGERS,
        )
        from tulip_storage.models import Base, Household

        eng = create_engine(
            f"sqlite:///{db_path}",
            future=True,
            connect_args={"timeout": busy_timeout_ms / 1000},
        )

        @event.listens_for(eng, "connect")
        def _enable_fk(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(eng)
        from sqlalchemy import text as _text

        with eng.begin() as conn:
            for ddl in INITIAL_TRIGGERS:
                conn.execute(_text(ddl))
            for ddl in P4_0_SHADOW_TRIGGERS:
                conn.execute(_text(ddl))

        sm = sessionmaker(eng, expire_on_commit=False)
        with sm() as s:
            h = Household(id=uuid4(), name="LockTest", base_currency="USD")
            s.add(h)
            s.commit()
            s.refresh(h)
        return eng, sm, h

    def test_second_writer_blocks_until_first_commits(self, tmp_path) -> None:
        """A held gate lock forces a concurrent gate to wait or error."""
        import sqlalchemy.exc

        from tulip_ai.cost import _acquire_household_gate_lock

        eng, sm, h = self._file_engine_and_household(tmp_path / "lock.db", busy_timeout_ms=100)
        try:
            s_a = sm()
            s_b = sm()
            try:
                # Session A grabs the lock (no commit; tx stays open).
                _acquire_household_gate_lock(s_a, h.id)

                # Session B tries to acquire the same lock — SQLite has
                # one global writer, so B's UPDATE must wait the 100ms
                # busy_timeout and then raise.
                import pytest as _pytest

                with _pytest.raises(sqlalchemy.exc.OperationalError) as exc_info:
                    _acquire_household_gate_lock(s_b, h.id)
                assert "locked" in str(exc_info.value).lower()
            finally:
                s_a.rollback()
                s_b.rollback()
                s_a.close()
                s_b.close()
        finally:
            eng.dispose()

    def test_lock_released_after_commit_allows_next_gate(self, tmp_path) -> None:
        """Committing the first transaction must release the lock."""
        from tulip_ai.cost import _acquire_household_gate_lock

        eng, sm, h = self._file_engine_and_household(tmp_path / "lock2.db")
        try:
            s_a = sm()
            try:
                _acquire_household_gate_lock(s_a, h.id)
                s_a.commit()
            finally:
                s_a.close()

            # Fresh session — lock must be available immediately.
            s_b = sm()
            try:
                _acquire_household_gate_lock(s_b, h.id)
                s_b.commit()
            finally:
                s_b.close()
        finally:
            eng.dispose()

    def test_enforce_pre_call_acquires_lock(self, tmp_path) -> None:
        """``enforce_pre_call`` blocks a concurrent gate on the same household."""
        import sqlalchemy.exc

        eng, sm, h = self._file_engine_and_household(tmp_path / "lock3.db", busy_timeout_ms=100)
        try:
            kwargs = {
                "household_id": h.id,
                "user_id": None,
                "rate_limit_per_hour": 60,
                "monthly_cost_cap_usd": None,
                "cost_cap_behaviour": "degrade",
                "fallback_provider": "ollama",
                "fallback_model": "llama3:70b",
                "primary_provider": "anthropic",
                "primary_model": "claude-opus-4-7",
            }

            s_a = sm()
            s_b = sm()
            try:
                result = enforce_pre_call(s_a, **kwargs)
                assert isinstance(result, PreCallApproval)
                # s_a's transaction is still open (no commit yet); the
                # lock from inside enforce_pre_call must still be held.
                import pytest as _pytest

                with _pytest.raises(sqlalchemy.exc.OperationalError):
                    enforce_pre_call(s_b, **kwargs)
            finally:
                s_a.rollback()
                s_b.rollback()
                s_a.close()
                s_b.close()
        finally:
            eng.dispose()
