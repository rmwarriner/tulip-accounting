"""Endpoints for ``/v1/periods`` (#136).

Surfaces the ``PeriodRepository`` close/reopen primitives so users can
run a month-end loop from ``tulip periods`` without falling back to a
direct DB write. Soft-close is the v1 model — closed periods reject
new transactions via the existing ``period.closed`` 400 path; this
router only changes the status, not the enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import PeriodNotFoundError, problem_response
from tulip_api.schemas.period import PeriodRead
from tulip_storage.models import Period as PeriodModel
from tulip_storage.models import PeriodStatus
from tulip_storage.repositories import AuditLogWriter, PeriodRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/periods", tags=["periods"])
log = structlog.get_logger("tulip_api.periods")


def _to_read(period: PeriodModel) -> PeriodRead:
    return PeriodRead(
        id=period.id,
        start_date=period.start_date,
        end_date=period.end_date,
        status=period.status.value,
        closed_at=period.closed_at,
        closed_by_user_id=period.closed_by_user_id,
    )


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


@router.get(
    "",
    response_model=list[PeriodRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_periods(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[PeriodRead]:
    """List the household's periods, newest first."""
    return [_to_read(p) for p in PeriodRepository(session, claims.household_id).list_all()]


@router.post(
    "/{period_id}/close",
    response_model=PeriodRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("period.not_found"),
    },
)
def close_period(
    period_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> PeriodRead:
    """Soft-close a period. Idempotent — closing an already-closed period is a no-op."""
    repo = PeriodRepository(session, claims.household_id)
    existing = repo.get(period_id)
    if existing is None:
        raise PeriodNotFoundError()
    if existing.status is PeriodStatus.SOFT_CLOSED:
        return _to_read(existing)

    before = {"status": existing.status.value, "closed_at": None}
    closed = repo.close(period_id, by_user_id=claims.user_id)
    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="period",
        entity_id=closed.id,
        before=before,
        after={
            "status": closed.status.value,
            "closed_at": closed.closed_at.isoformat() if closed.closed_at else None,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("period.closed", period_id=str(closed.id))
    return _to_read(closed)


@router.post(
    "/{period_id}/reopen",
    response_model=PeriodRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("period.not_found"),
    },
)
def reopen_period(
    period_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> PeriodRead:
    """Re-open a soft-closed period. Idempotent on already-open periods."""
    repo = PeriodRepository(session, claims.household_id)
    existing = repo.get(period_id)
    if existing is None:
        raise PeriodNotFoundError()
    if existing.status is PeriodStatus.OPEN:
        return _to_read(existing)

    before = {
        "status": existing.status.value,
        "closed_at": existing.closed_at.isoformat() if existing.closed_at else None,
    }
    reopened = repo.reopen(period_id)
    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="period",
        entity_id=reopened.id,
        before=before,
        after={"status": reopened.status.value, "closed_at": None},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("period.reopened", period_id=str(reopened.id))
    return _to_read(reopened)
