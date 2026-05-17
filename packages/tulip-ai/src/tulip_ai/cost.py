"""Pre-call cost-cap + rate-limit checks (ADR-0005 §Q7, P6.5.a).

Two pure functions called by every capability before the provider's
``chat()`` runs. Each consults ``ai_invocations`` for the current month
(cost) or the last 60 minutes (rate). The capability layer turns the
returned decision into either a permitted call, an audited cost-capped
row, or an explicit-Ollama swap on the ``degrade`` branch.

Per ADR §Q7 the cost cap is household-wide (not per-capability) and the
rate limit is per-user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import func, select, text

from tulip_storage.models import AIInvocation, AIOutcome

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


CostDecisionKind = Literal["allow", "cap_exceeded"]
RateDecisionKind = Literal["allow", "rate_limited"]


@dataclass(frozen=True, slots=True)
class CostDecision:
    """Outcome of a pre-call cost check."""

    kind: CostDecisionKind
    spent_so_far_usd: Decimal
    cap_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class RateDecision:
    """Outcome of a pre-call rate check."""

    kind: RateDecisionKind
    count_in_window: int
    limit_per_hour: int


DEFAULT_RATE_LIMIT_PER_HOUR = 60


def _month_start_utc(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _billable_outcomes() -> tuple[str, ...]:
    """Outcomes that count against the monthly cap.

    Successful and provider-error rows both spent provider quota; capped /
    rate-limited / disabled rows never reached the wire, so they don't.
    """
    return (AIOutcome.SUCCESS.value, AIOutcome.PROVIDER_ERROR.value)


def _acquire_household_gate_lock(session: Session, household_id: UUID) -> None:
    """Serialize concurrent pre-call gates on the same household (#334, M-23).

    The cost-cap + rate-limit gate reads `SUM(cost_estimate_usd)` and
    `COUNT(*)` from `ai_invocations`, then writes one new row. Without
    a write lock at the entry, two concurrent capabilities can both
    observe `spent < cap` and both commit success rows — the audit's
    classic TOCTOU race documented in M-23.

    Strategy:
    - SQLite: issue a no-op UPDATE on the household row. Any UPDATE in
      a SQLAlchemy session escalates the connection's transaction from
      DEFERRED to RESERVED, which is SQLite's single-writer chokepoint;
      concurrent gates serialize through here. The row content is
      unchanged (``SET id = id``), so this is purely a lock acquisition.
    - PostgreSQL (Phase 9): issue ``SELECT 1 FROM households ... FOR UPDATE``
      to acquire a row-level lock with the same serialization semantics
      without writing the row.

    The lock is held until the surrounding session's transaction commits —
    callers MUST commit (or rollback) after writing the matching
    ``ai_invocations`` row, otherwise the lock leaks and subsequent gates
    will block.
    """
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        session.execute(
            text("SELECT 1 FROM households WHERE id = :hid FOR UPDATE"),
            {"hid": str(household_id)},
        )
    else:
        # SQLite (and other dialects without FOR UPDATE — sqlite is the
        # only one in scope today). The no-op UPDATE promotes BEGIN to
        # RESERVED, blocking concurrent writers until commit.
        session.execute(
            text("UPDATE households SET id = id WHERE id = :hid"),
            {"hid": str(household_id)},
        )


def check_cost_cap(
    session: Session,
    *,
    household_id: UUID,
    estimated_cost_usd: Decimal,
    monthly_cap_usd: Decimal | None,
    now: datetime | None = None,
) -> CostDecision:
    """Decide whether one more call would breach the household's monthly cap.

    Returns ``allow`` when ``monthly_cap_usd`` is unset, or when
    ``spent + estimated <= cap``. Returns ``cap_exceeded`` otherwise.
    The caller is responsible for the audit row + the degrade/hard_fail
    branch.
    """
    if monthly_cap_usd is None:
        return CostDecision(kind="allow", spent_so_far_usd=Decimal("0"), cap_usd=None)

    when = now or datetime.now(UTC)
    month_start = _month_start_utc(when)

    total = session.execute(
        select(func.coalesce(func.sum(AIInvocation.cost_estimate_usd), 0)).where(
            AIInvocation.household_id == household_id,
            AIInvocation.created_at >= month_start,
            AIInvocation.outcome.in_(_billable_outcomes()),
        )
    ).scalar_one()

    spent = Decimal(str(total or 0))
    if spent + estimated_cost_usd > monthly_cap_usd:
        return CostDecision(kind="cap_exceeded", spent_so_far_usd=spent, cap_usd=monthly_cap_usd)
    return CostDecision(kind="allow", spent_so_far_usd=spent, cap_usd=monthly_cap_usd)


def check_rate_limit(
    session: Session,
    *,
    household_id: UUID,
    user_id: UUID | None,
    limit_per_hour: int | None = None,
    now: datetime | None = None,
) -> RateDecision:
    """Decide whether the calling user has exceeded their sliding-window quota.

    ``user_id`` may be ``None`` for system-driven calls (e.g. nightly
    scheduler); in that case the bucket is keyed on ``household_id`` with
    ``actor_user_id IS NULL`` so per-user buckets stay independent.
    """
    limit = limit_per_hour if limit_per_hour is not None else DEFAULT_RATE_LIMIT_PER_HOUR
    when = now or datetime.now(UTC)
    window_start = when - timedelta(hours=1)

    stmt = select(func.count()).where(
        AIInvocation.household_id == household_id,
        AIInvocation.created_at >= window_start,
    )
    if user_id is None:
        stmt = stmt.where(AIInvocation.actor_user_id.is_(None))
    else:
        stmt = stmt.where(AIInvocation.actor_user_id == user_id)

    count = int(session.execute(stmt).scalar_one() or 0)
    if count >= limit:
        return RateDecision(kind="rate_limited", count_in_window=count, limit_per_hour=limit)
    return RateDecision(kind="allow", count_in_window=count, limit_per_hour=limit)


@dataclass(frozen=True, slots=True)
class PreCallApproval:
    """The pre-call gate let this invocation through.

    ``degraded`` is True when cost-cap forced a swap to
    ``policy.fallback_provider`` / ``policy.fallback_model``. The capability
    must call the provider named in this approval, not the one named in
    the resolved policy, and audit ``provider=<this>`` so the explicit-Ollama
    rule from ADR-0005 §Q7 holds.
    """

    provider: str | None
    model: str | None
    degraded: bool


@dataclass(frozen=True, slots=True)
class PreCallBlock:
    """The pre-call gate rejected this invocation.

    ``outcome`` is the value the capability must stamp on its
    ``ai_invocations`` audit row before raising the matching exception
    (``AIRateLimited`` or ``AICostCapped``). ``reason`` is operator-facing
    diagnostic text.
    """

    outcome: Literal["rate_limited", "cost_capped"]
    reason: str


PreCallResult = PreCallApproval | PreCallBlock


def enforce_pre_call(
    session: Session,
    *,
    household_id: UUID,
    user_id: UUID | None,
    rate_limit_per_hour: int,
    monthly_cost_cap_usd: Decimal | None,
    cost_cap_behaviour: Literal["degrade", "hard_fail"],
    fallback_provider: str | None,
    fallback_model: str | None,
    primary_provider: str | None,
    primary_model: str | None,
    estimated_cost_usd: Decimal = Decimal("0"),
    now: datetime | None = None,
) -> PreCallResult:
    """Run rate-limit then cost-cap gates; return an actionable decision.

    Rate-limit fires first (always hard-fails — no degrade path). If the
    rate check passes, the cost check runs. On ``cap_exceeded`` with
    ``cost_cap_behaviour=degrade`` and a configured ``fallback_provider``,
    returns an approval that swaps provider/model. Otherwise, returns a
    block — the capability writes the matching audit row and raises.

    The whole check runs under a household write-lock (#334, M-23) so
    that two concurrent callers can't both observe ``spent < cap`` and
    both commit success rows. The lock releases when the surrounding
    session commits or rolls back, which is the same boundary the
    capability uses to persist the matching ``ai_invocations`` row.
    """
    _acquire_household_gate_lock(session, household_id)

    rate = check_rate_limit(
        session,
        household_id=household_id,
        user_id=user_id,
        limit_per_hour=rate_limit_per_hour,
        now=now,
    )
    if rate.kind == "rate_limited":
        return PreCallBlock(
            outcome="rate_limited",
            reason=(
                f"{rate.count_in_window} AI calls in the last hour exceeds "
                f"the {rate.limit_per_hour}/hour per-user limit."
            ),
        )

    cost = check_cost_cap(
        session,
        household_id=household_id,
        estimated_cost_usd=estimated_cost_usd,
        monthly_cap_usd=monthly_cost_cap_usd,
        now=now,
    )
    if cost.kind == "cap_exceeded":
        if cost_cap_behaviour == "degrade" and fallback_provider:
            return PreCallApproval(
                provider=fallback_provider,
                model=fallback_model,
                degraded=True,
            )
        return PreCallBlock(
            outcome="cost_capped",
            reason=(
                f"${cost.spent_so_far_usd} spent this month vs ${cost.cap_usd} cap; "
                f"cost_cap_behaviour={cost_cap_behaviour!r}."
            ),
        )

    return PreCallApproval(provider=primary_provider, model=primary_model, degraded=False)


__all__ = [
    "DEFAULT_RATE_LIMIT_PER_HOUR",
    "CostDecision",
    "PreCallApproval",
    "PreCallBlock",
    "PreCallResult",
    "RateDecision",
    "check_cost_cap",
    "check_rate_limit",
    "enforce_pre_call",
]
