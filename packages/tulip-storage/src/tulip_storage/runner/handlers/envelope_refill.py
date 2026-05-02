"""``envelope_refill`` runner handler — see ADR-0002 §4 and P4.3.b (#69).

Materializes an envelope's ``refill_rule`` into a ``REFILL`` shadow
transaction each time the scheduler fires. The runner passes the
:class:`ScheduledJob` (whose ``payload`` carries the envelope id) and the
runner's ``Clock``; the handler does the rest:

1. Loads the envelope and its ``refill_rule``.
2. Lazy-creates the household's ``Unallocated`` system pool for the
   envelope's currency if missing.
3. Computes ``current_balance`` from the shadow ledger.
4. Computes ``recent_inflow`` for ``PERCENTAGE_OF_INCOME`` strategies
   (sum of ``BUDGET_INFLOW`` shadow tx since the job's ``last_run_at``,
   or the last 30 days if first run).
5. Calls :func:`tulip_core.allocation.evaluate_refill_rule` (pure).
6. If the engine returns a non-zero amount, posts a 2-leg shadow tx
   (``Unallocated -X`` / envelope ``+X``) with reason ``REFILL`` and
   ``actor_kind="system"`` audit metadata.

The handler is constructed via :func:`make_envelope_refill_handler` —
the factory captures the runner's ``session_maker`` in a closure so the
handler signature stays the simple ``(job, clock)`` shape ADR-0002 §2
specifies.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tulip_core.allocation import (
    RefillRule,
    RefillStrategy,
    evaluate_refill_rule,
)
from tulip_core.allocation import (
    ShadowPosting as DomainShadowPosting,
)
from tulip_core.allocation import (
    ShadowTransaction as DomainShadowTransaction,
)
from tulip_core.allocation import (
    ShadowTxReason as DomainShadowTxReason,
)
from tulip_core.allocation import (
    ShadowTxStatus as DomainShadowTxStatus,
)
from tulip_core.money import Money
from tulip_storage.models import PoolType
from tulip_storage.repositories import (
    AllocationPoolRepository,
    AuditLogWriter,
    EnvelopeRepository,
    ShadowTransactionRepository,
)
from tulip_storage.runner.clock import Clock

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.runner import HandlerCallback


log = logging.getLogger("tulip_storage.runner.handlers.envelope_refill")


#: Lookback window for ``PERCENTAGE_OF_INCOME`` rules on the first fire
#: (when ``ScheduledJob.last_run_at`` is None). Subsequent fires use the
#: actual last run as the lower bound. 30 days matches the typical
#: "monthly" budgeting cadence.
FIRST_FIRE_INFLOW_LOOKBACK = timedelta(days=30)


class EnvelopeRefillError(RuntimeError):
    """Raised by the handler when the refill cannot proceed.

    Surfaces to the runner as a regular exception → the run is marked
    ``failed`` and retried per the runner's backoff policy. See ADR-0002
    §7. Distinct from ``ValueError`` so the runner's catch-all logging
    can differentiate handler-domain errors from generic bugs.
    """


def make_envelope_refill_handler(
    session_maker: sessionmaker[Session],
) -> HandlerCallback:
    """Build the ``envelope_refill`` handler bound to a session factory.

    Returns a callable matching :data:`HandlerCallback`. Call once at
    runner-construction time::

        runner = Runner(session_maker)
        runner.register_handler(
            "envelope_refill",
            make_envelope_refill_handler(session_maker),
        )

    The factory shape keeps the handler's signature ``(job, clock)``
    while still giving it access to a session — per ADR-0002 §2, handlers
    can't accept extra args without changing the runner's surface.
    """

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        envelope_id_raw = job.payload.get("envelope_id")
        if envelope_id_raw is None:
            msg = f"envelope_refill job {job.id} payload missing 'envelope_id'; cannot proceed"
            raise EnvelopeRefillError(msg)
        # Tolerate both UUID-as-string (JSON) and UUID-as-UUID (rare).
        envelope_id = UUID(envelope_id_raw) if isinstance(envelope_id_raw, str) else envelope_id_raw

        with session_maker() as session:
            _process(session, job=job, clock=clock, envelope_id=envelope_id)
            session.commit()

    return handle


def _process(
    session: Session,
    *,
    job: ScheduledJob,
    clock: Clock,
    envelope_id: UUID,
) -> None:
    """Inner: do all the work in one session scope. Caller commits."""
    # 1. Load envelope.
    found = EnvelopeRepository(session, job.household_id).get(envelope_id)
    if found is None:
        msg = f"envelope {envelope_id} not found in household {job.household_id} — cannot refill"
        raise EnvelopeRefillError(msg)
    pool, env = found

    if not pool.is_active:
        # Envelope was deactivated since the job was scheduled. Don't
        # error (which would retry); just no-op silently. Future-proof:
        # a separate cleanup pass could cancel orphan jobs.
        log.info(
            "envelope_refill.skipped_inactive",
            extra={"envelope_id": str(pool.id), "job_id": str(job.id)},
        )
        return

    if env.refill_rule_json is None:
        # Envelope has no rule — nothing to do. Same no-op shape.
        log.info(
            "envelope_refill.skipped_no_rule",
            extra={"envelope_id": str(pool.id), "job_id": str(job.id)},
        )
        return

    rule = RefillRule.from_dict(json.loads(env.refill_rule_json))

    # 2. Lazy-create / fetch the Unallocated system pool.
    pool_repo = AllocationPoolRepository(session, job.household_id)
    sys_pools = pool_repo.get_or_create_system_pools(currency=pool.currency)
    unallocated = sys_pools[PoolType.UNALLOCATED]

    # 3. Current balance.
    shadow_repo = ShadowTransactionRepository(session, job.household_id)
    balance_dict = shadow_repo.balance_for_pool(pool.id, currency=pool.currency)
    current_balance = Money(balance_dict.get(pool.currency, Decimal(0)), pool.currency)

    # 4. Recent inflow — only relevant for PERCENTAGE_OF_INCOME.
    recent_inflow: Money | None = None
    if rule.strategy is RefillStrategy.PERCENTAGE_OF_INCOME:
        since = _inflow_window_start(job, clock)
        inflow_total = shadow_repo.inflow_since(currency=pool.currency, since=since.date())
        recent_inflow = Money(inflow_total, pool.currency)

    # 5. Evaluate.
    refill_amount = evaluate_refill_rule(
        rule, current_balance=current_balance, recent_inflow=recent_inflow
    )
    if refill_amount.amount <= 0:
        log.info(
            "envelope_refill.no_contribution",
            extra={
                "envelope_id": str(pool.id),
                "job_id": str(job.id),
                "reason": rule.strategy.value,
            },
        )
        return

    # 6. Post the shadow tx + audit row.
    domain_tx = DomainShadowTransaction(
        id=uuid4(),
        household_id=job.household_id,
        date=clock().date(),
        description=f"Auto-refill: {pool.name}",
        reason=DomainShadowTxReason.REFILL,
        postings=(
            DomainShadowPosting(
                id=uuid4(),
                pool_id=unallocated.id,
                amount=Money(-refill_amount.amount, pool.currency),
                memo=f"Auto-refill from rule (strategy={rule.strategy.value})",
            ),
            DomainShadowPosting(
                id=uuid4(),
                pool_id=pool.id,
                amount=refill_amount,
                memo=f"Auto-refill into {pool.name}",
            ),
        ),
        status=DomainShadowTxStatus.POSTED,
        paired_main_tx_id=None,
        created_by_user_id=None,  # system-initiated
    )
    saved = shadow_repo.save_balanced(domain_tx)

    AuditLogWriter(session, job.household_id).write(
        action="create",
        actor_kind="system",
        actor_user_id=None,
        entity_type="shadow_transaction",
        entity_id=saved.id,
        after={
            "reason": "refill",
            "description": domain_tx.description,
            "amount": str(refill_amount.amount),
            "currency": refill_amount.currency,
            "envelope_id": str(pool.id),
            "scheduled_job_id": str(job.id),
            "rule_strategy": rule.strategy.value,
        },
        request_id=None,
    )

    log.info(
        "envelope_refill.completed",
        extra={
            "envelope_id": str(pool.id),
            "job_id": str(job.id),
            "amount": str(refill_amount.amount),
            "currency": refill_amount.currency,
        },
    )


def _inflow_window_start(job: ScheduledJob, clock: Clock) -> datetime:
    """Lower-bound for the recent-inflow query.

    First fire (no ``last_run_at``): use a 30-day lookback. Subsequent
    fires: use the actual ``last_run_at``.
    """
    now = clock()
    if job.last_run_at is None:
        return now - FIRST_FIRE_INFLOW_LOOKBACK
    last = job.last_run_at
    # SQLite DateTime(timezone=True) returns naive on read; normalize.
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return last
