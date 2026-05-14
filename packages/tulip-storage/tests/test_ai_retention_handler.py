"""Tests for the ``ai_retention`` runner handler (#243)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import AIInvocation, Household, PendingProposal
from tulip_storage.runner.handlers import AI_INVOCATION_RETENTION_DAYS, run_ai_retention

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


def _household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _invocation(session: Session, household_id: UUID, *, created_at: datetime) -> UUID:
    inv = AIInvocation(
        household_id=household_id,
        id=uuid4(),
        capability="nl_query",
        policy_resolved="permissive",
        profile="default",
        outcome="success",
        prompt_hash=b"\x00" * 32,
        created_at=created_at,
    )
    session.add(inv)
    session.commit()
    return inv.id


def test_retention_deletes_old_unlinked_keeps_recent_and_proposal_linked(
    session_maker: sessionmaker[Session],
) -> None:
    """Old non-proposal rows are GC'd; recent rows and proposal-linked rows survive."""
    now = datetime(2026, 6, 1, tzinfo=UTC)
    old = now - timedelta(days=AI_INVOCATION_RETENTION_DAYS + 100)
    recent = now - timedelta(days=10)

    with session_maker() as s:
        hh = _household(s)
        old_unlinked = _invocation(s, hh.id, created_at=old)
        recent_unlinked = _invocation(s, hh.id, created_at=recent)
        old_linked = _invocation(s, hh.id, created_at=old)
        # A pending proposal pins old_linked — it must survive the GC.
        s.add(
            PendingProposal(
                household_id=hh.id,
                id=uuid4(),
                kind="envelope_budget_update",
                title="keep me",
                payload={},
                status="pending",
                created_by_kind="ai_agent",
                ai_invocation_id=old_linked,
            )
        )
        s.commit()

    deleted = run_ai_retention(session_maker, now=now)

    assert deleted == 1
    with session_maker() as s:
        surviving = {r.id for r in s.execute(select(AIInvocation)).scalars().all()}
    assert old_unlinked not in surviving
    assert recent_unlinked in surviving
    assert old_linked in surviving


def test_retention_is_a_noop_when_nothing_is_stale(
    session_maker: sessionmaker[Session],
) -> None:
    """A run with only recent rows deletes nothing."""
    now = datetime(2026, 6, 1, tzinfo=UTC)
    with session_maker() as s:
        hh = _household(s)
        _invocation(s, hh.id, created_at=now - timedelta(days=1))

    assert run_ai_retention(session_maker, now=now) == 0
