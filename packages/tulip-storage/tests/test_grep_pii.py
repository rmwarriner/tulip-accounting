"""Tests for ``tulip_storage.grep_pii`` (#346 / privacy audit M-21).

The scanner walks household-scoped text + JSON columns and reports
substring matches against the supplied identifiers. These tests pin:

- Each scanned column class actually produces matches when the needle
  is present.
- An empty/clean household returns no matches (the "post-delete
  erasure looks clean" headline case).
- At least one needle is required; calling with all-None raises.
- Snippet shape (excerpt around the match, not the whole row).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.grep_pii import run_grep_pii
from tulip_storage.migrations._triggers import INITIAL_TRIGGERS, P4_0_SHADOW_TRIGGERS
from tulip_storage.models import (
    AIInvocation,
    AuditLog,
    Base,
    Household,
    Notification,
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


class TestGrepPiiHappyPath:
    def test_audit_log_after_snapshot_match(self, session_maker, household):
        with session_maker() as s:
            s.add(
                AuditLog(
                    id=uuid4(),
                    household_id=household.id,
                    occurred_at=datetime.now(tz=UTC),
                    actor_kind="user",
                    action="user.created",
                    entity_type="user",
                    entity_id=uuid4(),
                    after_snapshot={"email": "alice@example.com", "role": "admin"},
                )
            )
            s.commit()
            matches = run_grep_pii(s, household_id=household.id, email="alice@example.com")

        assert any(m.table == "audit_log" and m.column == "after_snapshot" for m in matches), (
            f"expected audit_log.after_snapshot hit; got {matches}"
        )

    def test_ai_invocations_prompt_json_match(self, session_maker, household):
        with session_maker() as s:
            s.add(
                AIInvocation(
                    household_id=household.id,
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
                    prompt_json='{"messages": "remembered alice@example.com"}',
                )
            )
            s.commit()
            matches = run_grep_pii(s, household_id=household.id, email="alice@example.com")

        assert any(m.table == "ai_invocations" and m.column == "prompt_json" for m in matches)

    def test_notification_body_match(self, session_maker, household):
        with session_maker() as s:
            s.add(
                Notification(
                    household_id=household.id,
                    id=uuid4(),
                    created_at=datetime.now(tz=UTC),
                    kind="forecast",
                    severity="info",
                    title="Envelope alert",
                    body="Alice — heads up about your Groceries envelope",
                    produced_by="daily_insights",
                    entity_type="envelope",
                    entity_id=uuid4(),
                )
            )
            s.commit()
            matches = run_grep_pii(s, household_id=household.id, display_name="Alice")

        assert any(m.table == "notifications" and m.column == "body" for m in matches)

    def test_multiple_needles_all_searched(self, session_maker, household):
        """All non-None / non-empty needles search; results accumulate."""
        with session_maker() as s:
            s.add(
                AuditLog(
                    id=uuid4(),
                    household_id=household.id,
                    occurred_at=datetime.now(tz=UTC),
                    actor_kind="user",
                    action="user.created",
                    entity_type="user",
                    entity_id=uuid4(),
                    after_snapshot={
                        "email": "bob@example.com",
                        "display_name": "Bob Smith",
                    },
                )
            )
            s.commit()
            matches = run_grep_pii(
                s,
                household_id=household.id,
                email="bob@example.com",
                display_name="Bob Smith",
            )

        # Same row matches both needles → two PiiMatch entries.
        needles = {m.needle for m in matches if m.table == "audit_log"}
        assert "bob@example.com" in needles
        assert "Bob Smith" in needles


class TestGrepPiiCleanHousehold:
    def test_empty_household_returns_no_matches(self, session_maker, household):
        with session_maker() as s:
            matches = run_grep_pii(s, household_id=household.id, email="alice@example.com")
        assert matches == []

    def test_match_disappears_after_row_deleted(self, session_maker, household):
        with session_maker() as s:
            row_id = uuid4()
            s.add(
                Notification(
                    household_id=household.id,
                    id=row_id,
                    created_at=datetime.now(tz=UTC),
                    kind="forecast",
                    severity="info",
                    title="Envelope alert",
                    body="Alice's spending is up",
                    produced_by="daily_insights",
                    entity_type="envelope",
                    entity_id=uuid4(),
                )
            )
            s.commit()
            before = run_grep_pii(s, household_id=household.id, display_name="Alice")
            assert len(before) == 1

            s.execute(
                text("DELETE FROM notifications WHERE id = :i"),
                {"i": str(row_id)},
            )
            s.commit()

            after = run_grep_pii(s, household_id=household.id, display_name="Alice")
            assert after == []


class TestGrepPiiInputs:
    def test_no_needles_raises(self, session_maker, household):
        with session_maker() as s, pytest.raises(ValueError, match="at least one"):
            run_grep_pii(s, household_id=household.id)

    def test_all_whitespace_needles_raises(self, session_maker, household):
        with session_maker() as s, pytest.raises(ValueError, match="at least one"):
            run_grep_pii(s, household_id=household.id, email="   ", display_name="")


class TestGrepPiiSnippet:
    def test_snippet_excerpts_around_match(self, session_maker, household):
        long_body = "x" * 200 + "alice@example.com" + "y" * 200
        with session_maker() as s:
            s.add(
                Notification(
                    household_id=household.id,
                    id=uuid4(),
                    created_at=datetime.now(tz=UTC),
                    kind="forecast",
                    severity="info",
                    title="Envelope alert",
                    body=long_body,
                    produced_by="daily_insights",
                    entity_type="envelope",
                    entity_id=uuid4(),
                )
            )
            s.commit()
            matches = run_grep_pii(s, household_id=household.id, email="alice@example.com")

        assert len(matches) == 1
        snippet = matches[0].snippet
        # Snippet must contain the needle and ellipses (not the whole body).
        assert "alice@example.com" in snippet
        assert snippet.startswith("…")
        assert snippet.endswith("…")
        # And it must be shorter than the body.
        assert len(snippet) < len(long_body)
