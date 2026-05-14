"""``ai_retention`` runner handler — TTL garbage-collection of ai_invocations.

``ai_invocations`` is append-only by design (ADR-0005 §Q6): every row
carries ``prompt_hash``; ``prompt_json`` / ``response_text`` land only
when the household has ``log_prompts=true``. Even hash-only rows
accumulate forever otherwise — and ``prompt_hash`` is pseudonymous over
a small per-household input space (#243 / audit M-5).

This handler deletes non-proposal-linked ``ai_invocations`` rows older
than ``AI_INVOCATION_RETENTION_DAYS``. A row is "proposal-linked" — and
therefore preserved — while any ``pending_proposals`` row still
references it via ``pending_proposals.ai_invocation_id``; once that
proposal is gone (e.g. rejected + deleted per #240) the invocation
becomes eligible for collection. See H-16 in #243.

Mirrors the ``attachment_gc`` GC-handler shape: a pure ``run_*`` helper
(testable with an explicit ``now``) plus a ``make_*_handler`` factory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from tulip_storage.models import AIInvocation, PendingProposal

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.clock import Clock
    from tulip_storage.runner.runner import HandlerCallback

log = logging.getLogger("tulip_storage.runner.ai_retention")

#: Non-proposal-linked ai_invocations older than this are GC'd. Surfaced
#: read-only in ``GET /v1/ai/config`` so operators can see the policy.
AI_INVOCATION_RETENTION_DAYS: int = 90


def run_ai_retention(
    session_maker: sessionmaker[Session],
    *,
    now: datetime,
    retention_days: int = AI_INVOCATION_RETENTION_DAYS,
) -> int:
    """Delete ai_invocations older than the TTL, excluding proposal-linked rows.

    Pure-ish helper for tests: takes ``now`` explicitly so a test can
    simulate "old" rows without waiting. Runs across all households (a
    periodic system GC, like ``attachment_gc``). Returns the count of
    rows deleted.
    """
    cutoff = now - timedelta(days=retention_days)
    with session_maker() as session:
        proposal_linked = select(PendingProposal.ai_invocation_id).where(
            PendingProposal.ai_invocation_id.is_not(None)
        )
        result = session.execute(
            delete(AIInvocation).where(
                AIInvocation.created_at < cutoff,
                AIInvocation.id.not_in(proposal_linked),
            )
        )
        session.commit()
        # session.execute() on a bulk DELETE returns a CursorResult at runtime;
        # the Session.execute signature only narrows to Result.
        deleted = cast("CursorResult[Any]", result).rowcount or 0
    if deleted:
        log.info("ai_retention.summary", extra={"deleted": deleted})
    return deleted


def make_ai_retention_handler(session_maker: sessionmaker[Session]) -> HandlerCallback:
    """Build the ``ai_retention`` handler bound to a session factory.

    Register at runner construction time alongside the other handlers::

        runner.register_handler(
            "ai_retention", make_ai_retention_handler(session_maker)
        )
    """

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        run_ai_retention(session_maker, now=datetime.now(tz=UTC))

    return handle
