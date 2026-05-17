"""Admin / operator endpoints for audit-log retention (#245).

Three admin-only surfaces:

* ``GET /v1/admin/audit-policy`` — resolved per-tier retention (operator
  overrides merged with code defaults).
* ``PUT /v1/admin/audit-policy`` — patch one or more tiers. Writes an
  ``audit_log(action="household.audit_policy_set")`` consent-style row
  so the toggle history is itself in the audit trail.
* ``POST /v1/admin/audit-prune`` — synchronously invoke
  ``run_audit_retention`` for the caller's household. The daily handler
  runs across every household; this is the ops-debugging trigger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_api.auth.deps import require_role
from tulip_api.deps import get_session
from tulip_api.errors import problem_response
from tulip_api.schemas.admin import (
    AuditPruneResult,
    AuditRetentionPolicyPatch,
    AuditRetentionPolicyRead,
    GrepPiiMatchRead,
    GrepPiiResult,
)
from tulip_storage.grep_pii import run_grep_pii
from tulip_storage.models import Household
from tulip_storage.repositories import AuditLogWriter
from tulip_storage.runner.handlers.audit_retention import (
    _TIER_DEFAULTS,
    run_audit_retention,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/admin", tags=["admin"])
log = structlog.get_logger("tulip_api.admin")


def _resolved_policy(household: Household) -> AuditRetentionPolicyRead:
    """Merge operator override JSON with code defaults into the read shape."""
    policy = household.audit_retention_policy or {}

    def _resolve(key: str) -> int:
        raw = policy.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
        return _TIER_DEFAULTS[key]

    return AuditRetentionPolicyRead(
        ledger_days=_resolve("ledger_days"),
        auth_days=_resolve("auth_days"),
        ai_days=_resolve("ai_days"),
        admin_days=_resolve("admin_days"),
        default_days=_resolve("default_days"),
    )


@router.get(
    "/audit-policy",
    response_model=AuditRetentionPolicyRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def get_audit_policy(
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AuditRetentionPolicyRead:
    """Return the caller's household's resolved audit-retention policy (#245)."""
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101 — authenticated households always exist
    return _resolved_policy(household)


@router.put(
    "/audit-policy",
    response_model=AuditRetentionPolicyRead,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def put_audit_policy(
    body: AuditRetentionPolicyPatch,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AuditRetentionPolicyRead:
    """Patch one or more retention tiers (#245).

    Sending ``null`` (or omitting a key) leaves the tier at its existing
    override; passing a positive integer overrides. The audit row
    captures the full before/after of the stored JSON so consent
    provenance is answerable in the same way as the AI config audit
    (#247).
    """
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    before_policy: dict[str, Any] = dict(household.audit_retention_policy or {})
    after_policy: dict[str, Any] = dict(before_policy)
    fields = body.model_dump(exclude_none=True)
    after_policy.update(fields)
    household.audit_retention_policy = after_policy

    if before_policy != after_policy:
        AuditLogWriter(session, claims.household_id).write(
            action="household.audit_policy_set",
            actor_kind="user",
            actor_user_id=claims.user_id,
            entity_type="household",
            entity_id=claims.household_id,
            before=before_policy,
            after=after_policy,
            request_id=_request_uuid(request),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    session.commit()
    log.info(
        "admin.audit_policy_set",
        household_id=str(claims.household_id),
        fields=list(fields.keys()),
    )
    return _resolved_policy(household)


@router.post(
    "/audit-prune",
    response_model=AuditPruneResult,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def post_audit_prune(
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AuditPruneResult:
    """Synchronously prune the caller's household (#245).

    Invokes ``run_audit_retention`` scoped to ``claims.household_id``
    and returns the per-tier deletion counts. The daily scheduled
    handler runs across every household; this endpoint is for
    ops-debugging when an operator wants to see retention work without
    waiting for the next fire.
    """
    session_maker_factory = session.get_bind()
    # The endpoint shares the request's session for everything *else*
    # we audit; ``run_audit_retention`` opens its own session per call
    # (it commits at the end). Reuse the bind so we run against the same
    # DB without nesting transactions.
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    bound_session_maker = _sessionmaker(session_maker_factory, expire_on_commit=False)
    summary = run_audit_retention(
        bound_session_maker,
        now=datetime.now(tz=UTC),
        household_id=claims.household_id,
    )
    per_tier = summary.get(claims.household_id, {})
    total = sum(per_tier.values())
    log.info(
        "admin.audit_prune",
        household_id=str(claims.household_id),
        total_deleted=total,
    )
    return AuditPruneResult(
        household_id=claims.household_id,
        deleted_per_tier=per_tier,
        total_deleted=total,
    )


@router.get(
    "/grep-pii",
    response_model=GrepPiiResult,
    status_code=status.HTTP_200_OK,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def get_grep_pii(
    request: Request,
    user_id: UUID | None = None,
    email: str | None = None,
    display_name: str | None = None,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> GrepPiiResult:
    """Scan household-scoped text/JSON columns for PII identifiers (#346).

    Post-delete erasure verification: after ``DELETE /v1/users/{id}``
    the operator runs this to prove "yes, the deleted user's PII is
    actually gone" — or to surface what still needs scrubbing.

    At least one of ``user_id`` / ``email`` / ``display_name`` must be
    supplied. The scan is tenant-scoped to the caller's household;
    cross-household visibility is intentionally absent.

    The scan itself is recorded as ``admin.grep_pii_run`` so the audit
    chain captures the operator's verification step — the row carries
    needle *types* but not their values (no PII in the audit body).
    """
    try:
        matches = run_grep_pii(
            session,
            household_id=claims.household_id,
            user_id=user_id,
            email=email,
            display_name=display_name,
        )
    except ValueError as exc:
        from tulip_api.errors import TulipProblem

        raise TulipProblem(
            code="request.body_invalid",
            title="grep-pii requires at least one needle",
            status=400,
            detail=str(exc),
        ) from exc

    needles_provided = [
        kind
        for kind, value in (
            ("user_id", user_id),
            ("email", email),
            ("display_name", display_name),
        )
        if value
    ]
    AuditLogWriter(session, claims.household_id).write(
        action="admin.grep_pii_run",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="household",
        entity_id=claims.household_id,
        # No PII bytes in the audit body — just the structural fact +
        # the match count.
        metadata={
            "needle_kinds": needles_provided,
            "match_count": len(matches),
        },
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info(
        "admin.grep_pii",
        household_id=str(claims.household_id),
        needle_kinds=needles_provided,
        match_count=len(matches),
    )
    return GrepPiiResult(
        household_id=claims.household_id,
        needles=needles_provided,
        matches=[
            GrepPiiMatchRead(
                table=m.table,
                column=m.column,
                row_id=m.row_id,
                snippet=m.snippet,
                needle=m.needle,
            )
            for m in matches
        ],
    )


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


__all__ = ["router"]
