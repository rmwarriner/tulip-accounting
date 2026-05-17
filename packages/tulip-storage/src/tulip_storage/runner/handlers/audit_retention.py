"""``audit_retention`` runner handler — tiered TTL pruning of ``audit_log`` (#245).

The deep privacy audit (M-1) flagged ``audit_log`` "forever" retention as
a GDPR Art. 5(1)(e) storage-limitation gap: every row carries
``before_snapshot`` / ``after_snapshot`` JSON embedding user-typed
descriptions, emails, account names, etc., and rows survive deletion of
the underlying entity. "Accounting forensics" supports 5-7 years for
tax-relevant entities but supports nothing for "forever."

The handler maps each ``audit_log.action`` into one of five retention
tiers, reads each tier's day-count from ``households.audit_retention_policy``
(falling through to ``_TIER_DEFAULTS`` for any unset tier), and issues
one DELETE per tier per household. After pruning, an ``audit.pruned``
summary row is written with ``metadata={"deleted_per_tier": {...}}``
mirroring the ``ai.prompt_log_scrubbed`` pattern (#243). The summary
row itself lives in the ``admin_days`` tier so the trail outlives daily
fires.

Tier mapping is by ``action`` string (a static dict, see
``_RETENTION_TIER_BY_ACTION``) rather than a new ``audit_log.retention_tier``
column. This avoids back-filling every existing row at the cost of
requiring future action-strings to be tier-classified explicitly; the
architecture / coverage test in ``tulip-api/tests/test_audit_retention_coverage.py``
guards the drift.

Mirrors the ``ai_retention`` GC-handler shape: a pure ``run_*`` helper
(testable with an explicit ``now``) plus a ``make_*_handler`` factory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from tulip_storage.models import AuditLog, Household
from tulip_storage.repositories.audit_log import AuditLogWriter

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.clock import Clock
    from tulip_storage.runner.runner import HandlerCallback

log = logging.getLogger("tulip_storage.runner.audit_retention")

#: Default per-tier retention in days. Operators override via
#: ``households.audit_retention_policy``. The 7-year ledger floor is
#: anchored on US IRS record-keeping rules (Form 1040 supporting records:
#: 7 years for bad-debt deduction); other jurisdictions adjust via the
#: column (HMRC = 6y, ATO = 5y, see ``docs/USER_RIGHTS.md``).
_TIER_DEFAULTS: dict[str, int] = {
    "ledger_days": 2555,  # ~7 years — anything that mutates the ledger
    "auth_days": 90,  # login / refresh / MFA enrolment / password
    "ai_days": 30,  # consent toggles, prompt-scrubs, AI key ops, proposals
    "admin_days": 365,  # user / household deletion, csv-profile lifecycle
    "default_days": 90,  # safety net for any unmapped action string
}

#: Static action → tier map. **Every new audit action MUST be added
#: here**; the coverage test in
#: ``packages/tulip-api/tests/test_audit_retention_coverage.py`` crawls
#: the routers and asserts no surprises. Unmapped actions fall through
#: to ``default_days`` (90 days) — that's the safety net, but explicit
#: mapping is the policy.
_RETENTION_TIER_BY_ACTION: dict[str, str] = {
    # ----- ledger mutations (7y) ----------------------------------------
    "create": "ledger_days",
    "update": "ledger_days",
    "delete": "ledger_days",
    "void": "ledger_days",
    "description_rectified": "ledger_days",
    "redact": "ledger_days",
    "import_create": "ledger_days",
    "import_apply": "ledger_days",
    "statement_line_promote": "ledger_days",
    "reconciliation_create": "ledger_days",
    "reconciliation_revert": "ledger_days",
    "reconciliation_auto_match": "ledger_days",
    "reconciliation_carry_forward_add": "ledger_days",
    "reconciliation_carry_forward_remove": "ledger_days",
    "reconciliation_complete": "ledger_days",
    "reconciliation_match_create_manual": "ledger_days",
    "reconciliation_match_create_paper": "ledger_days",
    "reconciliation_match_reject": "ledger_days",
    "add-carry-forward": "ledger_days",
    "remove-carry-forward": "ledger_days",
    "auto-match": "ledger_days",
    "complete": "ledger_days",
    "create-manual-match": "ledger_days",
    "create-paper-match": "ledger_days",
    "period_close": "ledger_days",
    "period_reopen": "ledger_days",
    "refill_schedule.create": "ledger_days",
    "refill_schedule.cancel": "ledger_days",
    # ----- auth events (90d) -------------------------------------------
    "register": "auth_days",
    "login": "auth_days",
    "login_failed": "auth_days",
    "login_mfa_success": "auth_days",
    "password_changed": "auth_days",
    "profile_updated": "auth_days",
    "auth.refresh": "auth_days",
    "auth.logout": "auth_days",
    "mfa.enroll": "auth_days",
    "mfa.verify": "auth_days",
    "mfa.code_rejected": "auth_days",
    "mfa.recovery_codes_generated": "auth_days",
    "mfa.recovery_codes_regenerated": "auth_days",
    "mfa.recovery_login": "auth_days",
    "mfa.recovery_rejected": "auth_days",
    # ----- AI capability + consent (30d) -------------------------------
    "ai_invoke": "ai_days",
    "ai_approve": "ai_days",
    "ai_reject": "ai_days",
    "ai.consent_changed": "ai_days",
    "ai.prompt_log_scrubbed": "ai_days",
    "user.ai_policy_set": "ai_days",
    "user.ai_key_set": "ai_days",
    "user.ai_key_forgotten": "ai_days",
    "proposal.create": "ai_days",
    "proposal.approve": "ai_days",
    "proposal.reject": "ai_days",
    "proposal.delete": "ai_days",
    # ----- admin / lifecycle (365d) ------------------------------------
    "user.deleted": "admin_days",
    "user.data_exported": "admin_days",
    "household.deleted": "admin_days",
    "household.erase_requested": "admin_days",
    "household.audit_policy_set": "admin_days",
    "audit.pruned": "admin_days",  # the prune handler's own summary row
    "csv_profile_create": "admin_days",
    "csv_profile_update": "admin_days",
    "csv_profile_delete": "admin_days",
    "csv_profile_import": "admin_days",
}


def _resolve_tier_days(policy: dict[str, Any], tier_key: str) -> int:
    """Return the day-count for ``tier_key``, falling through to defaults.

    ``policy`` is the household's ``audit_retention_policy`` JSON. If
    the key is absent or its value isn't a positive int, the code default
    from ``_TIER_DEFAULTS`` wins — operator typos shouldn't disable a tier.
    """
    raw = policy.get(tier_key)
    if isinstance(raw, int) and raw > 0:
        return raw
    return _TIER_DEFAULTS[tier_key]


def run_audit_retention(
    session_maker: sessionmaker[Session],
    *,
    now: datetime,
    household_id: UUID | None = None,
) -> dict[UUID, dict[str, int]]:
    """Prune ``audit_log`` rows past their per-tier TTL (#245).

    Pure-ish helper for tests: takes ``now`` explicitly so a test can
    simulate "old" rows without waiting. When ``household_id`` is None
    the handler runs across every household (the daily-handler shape);
    when set it limits to that one tenant (the synchronous-CLI shape).

    Returns ``{household_id: {tier_key: rows_deleted}}`` so callers can
    surface per-tier counts.
    """
    summary: dict[UUID, dict[str, int]] = {}
    with session_maker() as session:
        # M-22 (#333): the audit_log BEFORE DELETE trigger blocks every
        # row delete; this is one of the two legitimate carve-out sites
        # (the other is household-erasure). The context manager drops +
        # recreates the trigger; the try/finally on its exit guarantees
        # the trigger is reinstated even if pruning fails partway.
        from tulip_storage.audit_log_helpers import audit_log_deletion_allowed

        with audit_log_deletion_allowed(session):
            return _run_audit_retention_inner(session, now, household_id, summary)


def _run_audit_retention_inner(
    session: Session,
    now: datetime,
    household_id: UUID | None,
    summary: dict[UUID, dict[str, int]],
) -> dict[UUID, dict[str, int]]:
    """The retention-prune body — extracted so the temp-marker bracket above stays tight."""
    targets = (
        session.execute(select(Household)).scalars().all()
        if household_id is None
        else [h for h in [session.get(Household, household_id)] if h is not None]
    )
    for household in targets:
        policy = household.audit_retention_policy or {}
        per_tier: dict[str, int] = {}
        for tier_key in _TIER_DEFAULTS:
            actions = tuple(
                action
                for action, mapped_tier in _RETENTION_TIER_BY_ACTION.items()
                if mapped_tier == tier_key
            )
            cutoff = now - timedelta(days=_resolve_tier_days(policy, tier_key))
            if tier_key == "default_days":
                # Fall-through bucket: any action not in the static map
                # ages at default_days. We can't enumerate "anything
                # except the mapped ones" cheaply in SQL, so build a
                # NOT-IN against every mapped action.
                all_mapped = tuple(_RETENTION_TIER_BY_ACTION.keys())
                where_clauses = [
                    AuditLog.household_id == household.id,
                    AuditLog.occurred_at < cutoff,
                    AuditLog.action.not_in(all_mapped),
                ]
            elif actions:
                where_clauses = [
                    AuditLog.household_id == household.id,
                    AuditLog.occurred_at < cutoff,
                    AuditLog.action.in_(actions),
                ]
            else:
                per_tier[tier_key] = 0
                continue
            result = session.execute(delete(AuditLog).where(*where_clauses))
            per_tier[tier_key] = cast("CursorResult[Any]", result).rowcount or 0

        total = sum(per_tier.values())
        if total > 0:
            # Summary row mirrors the ai.prompt_log_scrubbed pattern (#243).
            # actor_user_id is None — this is system GC, not a user action.
            # Routes through AuditLogWriter per the chokepoint invariant
            # enforced by test_architecture_audit_log_writer_only (#331).
            AuditLogWriter(session, household.id).write(
                action="audit.pruned",
                actor_kind="system",
                entity_type="household",
                entity_id=household.id,
                metadata={"deleted_per_tier": per_tier},
            )
        summary[household.id] = per_tier
    session.commit()
    if summary:
        log.info(
            "audit_retention.summary",
            extra={
                "households": len(summary),
                "total_deleted": sum(sum(per_tier.values()) for per_tier in summary.values()),
            },
        )
    return summary


def make_audit_retention_handler(
    session_maker: sessionmaker[Session],
) -> HandlerCallback:
    """Build the ``audit_retention`` handler bound to a session factory (#245)."""

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        run_audit_retention(session_maker, now=datetime.now(tz=UTC))

    return handle
