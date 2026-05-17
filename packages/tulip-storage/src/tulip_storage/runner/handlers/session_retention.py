"""``session_retention`` runner handler — daily prune of revoked sessions + used MFA codes (#344).

Per the deep privacy audit's M-6: ``sessions`` and ``mfa_recovery_codes``
accumulate indefinitely. Revoked sessions retain ``ip_address`` +
``user_agent`` forever; used recovery codes retain their ``used_at``
timestamp. GDPR Art. 5(1)(e) "storage limitation" — personal data kept
no longer than necessary. The operational tail for forensic value is
typically 30-90 days; 90 is the default chosen here to match the
``auth_days`` audit-log retention tier.

The handler deletes:
- ``sessions`` rows where ``revoked_at < now() - session_retention_days``
- ``mfa_recovery_codes`` rows where ``used_at < now() - session_retention_days``

Active sessions (``revoked_at IS NULL``) and unused recovery codes
(``used_at IS NULL``) are NEVER touched — they're load-bearing for
ongoing authentication.

Retention policy lives in ``households.audit_retention_policy`` JSON
under a new ``session_retention_days`` key, alongside the existing
audit-log tier keys. Falls through to ``_DEFAULT_SESSION_RETENTION_DAYS``
(90) if unset.

After pruning, an ``session.pruned`` audit row is written per household
with ``metadata={"sessions_deleted": N, "recovery_codes_deleted": M}``
mirroring the ``audit.pruned`` pattern (#245).

Mirrors the ``audit_retention`` GC-handler shape: a pure ``run_*``
helper (testable with an explicit ``now``) plus a ``make_*_handler``
factory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from tulip_storage.audit_log_helpers import audit_log_deletion_allowed
from tulip_storage.models import Household, MfaRecoveryCode
from tulip_storage.models import Session as SessionRow
from tulip_storage.repositories.audit_log import AuditLogWriter

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.clock import Clock
    from tulip_storage.runner.runner import HandlerCallback

log = logging.getLogger("tulip_storage.runner.session_retention")

#: Default retention window. Operators override via
#: ``households.audit_retention_policy.session_retention_days``. 90 days
#: matches the ``auth_days`` audit-log tier; the two are semantically
#: paired (a deleted session loses its IP/UA at the same horizon the
#: corresponding ``login``/``logout`` audit row ages out).
_DEFAULT_SESSION_RETENTION_DAYS: int = 90


def _resolve_session_retention_days(policy: dict[str, Any]) -> int:
    """Return the session-retention day count, falling through to the default.

    Operator-set values must be positive ints; anything else (missing
    key, malformed JSON, zero, negative) falls through to the default
    so a typo can't accidentally disable retention.
    """
    raw = policy.get("session_retention_days")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _DEFAULT_SESSION_RETENTION_DAYS


def run_session_retention(
    session_maker: sessionmaker[Session],
    *,
    now: datetime,
    household_id: UUID | None = None,
) -> dict[UUID, dict[str, int]]:
    """Prune revoked sessions + used MFA recovery codes past their retention window.

    Pure-ish helper for tests: takes ``now`` explicitly so a test can
    simulate "old" rows without waiting. When ``household_id`` is None
    the handler runs across every household; when set it limits to that
    one tenant.

    Returns ``{household_id: {"sessions_deleted": N,
    "recovery_codes_deleted": M}}`` per household processed.
    """
    summary: dict[UUID, dict[str, int]] = {}
    with session_maker() as session:
        # M-22 (#333): the audit_log BEFORE DELETE trigger blocks every
        # row delete; the per-household audit summary write below is an
        # INSERT (allowed), but we still need the carve-out in case a
        # household-erasure interleaves. Brackets are cheap and keep the
        # pattern consistent with the audit-retention handler.
        with audit_log_deletion_allowed(session):
            targets = (
                session.execute(select(Household)).scalars().all()
                if household_id is None
                else [h for h in [session.get(Household, household_id)] if h is not None]
            )
            for household in targets:
                policy = household.audit_retention_policy or {}
                days = _resolve_session_retention_days(policy)
                cutoff = now - timedelta(days=days)

                # Sessions: only revoked ones (NULL = still active, never
                # touch). Per-household scope on the FK.
                sessions_result = session.execute(
                    delete(SessionRow).where(
                        SessionRow.household_id == household.id,
                        SessionRow.revoked_at.is_not(None),
                        SessionRow.revoked_at < cutoff,
                    )
                )
                sessions_deleted = cast("CursorResult[Any]", sessions_result).rowcount or 0

                # MFA recovery codes: only used ones (NULL = still
                # bookmark for future login).
                codes_result = session.execute(
                    delete(MfaRecoveryCode).where(
                        MfaRecoveryCode.household_id == household.id,
                        MfaRecoveryCode.used_at.is_not(None),
                        MfaRecoveryCode.used_at < cutoff,
                    )
                )
                codes_deleted = cast("CursorResult[Any]", codes_result).rowcount or 0

                per_household = {
                    "sessions_deleted": int(sessions_deleted),
                    "recovery_codes_deleted": int(codes_deleted),
                }

                if sessions_deleted + codes_deleted > 0:
                    # Summary row mirrors the audit.pruned pattern (#245).
                    AuditLogWriter(session, household.id).write(
                        action="session.pruned",
                        actor_kind="system",
                        entity_type="household",
                        entity_id=household.id,
                        metadata=per_household,
                    )
                summary[household.id] = per_household
            session.commit()

    if summary:
        log.info(
            "session_retention.summary",
            extra={
                "households": len(summary),
                "total_sessions": sum(v["sessions_deleted"] for v in summary.values()),
                "total_codes": sum(v["recovery_codes_deleted"] for v in summary.values()),
            },
        )
    return summary


def make_session_retention_handler(
    session_maker: sessionmaker[Session],
) -> HandlerCallback:
    """Build the ``session_retention`` handler bound to a session factory (#344)."""

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        run_session_retention(session_maker, now=datetime.now(tz=UTC))

    return handle


__all__ = [
    "_DEFAULT_SESSION_RETENTION_DAYS",
    "make_session_retention_handler",
    "run_session_retention",
]
