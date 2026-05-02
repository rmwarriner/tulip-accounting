"""GET / POST / PATCH / DELETE / balance for /v1/sinking-funds.

Mirror of envelopes.py minus refill (sinking-fund contributions ride
through transfer or — eventually — a scheduled-tx runner in P4.3).
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    ForbiddenError,
    SinkingFundNotFoundError,
    problem_response,
)
from tulip_api.routers._pool_helpers import filter_for_role
from tulip_api.schemas.pool import PoolBalanceRead
from tulip_api.schemas.sinking_fund import (
    SinkingFundCreate,
    SinkingFundRead,
    SinkingFundUpdate,
)
from tulip_core.money import Money
from tulip_storage.models import (
    AllocationPool,
    ContributionStrategy,
    SinkingFund,
)
from tulip_storage.repositories import (
    AllocationPoolRepository,
    AuditLogWriter,
    ShadowTransactionRepository,
    SinkingFundRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/sinking-funds", tags=["sinking-funds"])
log = structlog.get_logger("tulip_api.sinking_funds")


def _to_read(pool: AllocationPool, sf: SinkingFund) -> SinkingFundRead:
    return SinkingFundRead(
        id=pool.id,
        name=pool.name,
        currency=pool.currency,
        visibility=pool.visibility,
        is_active=pool.is_active,
        target_amount=sf.target_amount,
        target_date=sf.target_date,
        contribution_strategy=sf.contribution_strategy.value,
        contribution_amount=sf.contribution_amount,
    )


@router.get(
    "",
    response_model=list[SinkingFundRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_sinking_funds(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[SinkingFundRead]:
    """List active sinking funds visible to the caller."""
    repo = SinkingFundRepository(session, claims.household_id)
    rows = [(p, s) for p, s in repo.list_active() if filter_for_role(p, claims)]
    return [_to_read(p, s) for p, s in rows]


@router.post(
    "",
    response_model=SinkingFundRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def create_sinking_fund(
    body: SinkingFundCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> SinkingFundRead:
    """Create a new sinking fund in the caller's household."""
    pool, sf = SinkingFundRepository(session, claims.household_id).create(
        name=body.name,
        currency=body.currency,
        target_amount=body.target_amount,
        target_date=body.target_date,
        contribution_strategy=ContributionStrategy(body.contribution_strategy),
        contribution_amount=body.contribution_amount,
        visibility=body.visibility,
        created_by_user_id=claims.user_id,
    )
    AuditLogWriter(session, claims.household_id).write(
        action="create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="sinking_fund",
        entity_id=pool.id,
        after={
            "name": pool.name,
            "currency": pool.currency,
            "visibility": pool.visibility,
            "target_amount": str(sf.target_amount),
            "target_date": sf.target_date.isoformat(),
            "contribution_strategy": sf.contribution_strategy.value,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("sinking_fund.created", sinking_fund_id=str(pool.id))
    return _to_read(pool, sf)


@router.get(
    "/{sinking_fund_id}",
    response_model=SinkingFundRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("sinking_fund.not_found"),
    },
)
def get_sinking_fund(
    sinking_fund_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> SinkingFundRead:
    """Fetch a sinking fund by id (404 if not in household or not visible)."""
    found = SinkingFundRepository(session, claims.household_id).get(sinking_fund_id)
    if found is None:
        raise SinkingFundNotFoundError()
    pool, sf = found
    if not filter_for_role(pool, claims):
        raise SinkingFundNotFoundError()
    return _to_read(pool, sf)


@router.patch(
    "/{sinking_fund_id}",
    response_model=SinkingFundRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("sinking_fund.not_found"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def update_sinking_fund(
    sinking_fund_id: UUID,
    body: SinkingFundUpdate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> SinkingFundRead:
    """Update mutable fields. Member cannot edit private sinking funds they didn't create."""
    repo = SinkingFundRepository(session, claims.household_id)
    found = repo.get(sinking_fund_id)
    if found is None:
        raise SinkingFundNotFoundError()
    pool, sf = found
    if not filter_for_role(pool, claims):
        raise SinkingFundNotFoundError()
    if (
        claims.role == "member"
        and pool.visibility == "private"
        and pool.created_by_user_id != claims.user_id
    ):
        raise ForbiddenError(
            "Members can only edit private sinking funds they created themselves. "
            "Ask an admin, or have the original creator make the change."
        )

    before = {
        "name": pool.name,
        "visibility": pool.visibility,
        "target_amount": str(sf.target_amount),
        "target_date": sf.target_date.isoformat(),
        "contribution_strategy": sf.contribution_strategy.value,
        "contribution_amount": (
            str(sf.contribution_amount) if sf.contribution_amount is not None else None
        ),
    }

    pool, sf = repo.update_fields(
        sinking_fund_id,
        name=body.name,
        visibility=body.visibility,
        target_amount=body.target_amount,
        target_date=body.target_date,
        contribution_strategy=(
            ContributionStrategy(body.contribution_strategy) if body.contribution_strategy else None
        ),
        contribution_amount=body.contribution_amount,
    )

    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="sinking_fund",
        entity_id=pool.id,
        before=before,
        after={
            "name": pool.name,
            "visibility": pool.visibility,
            "target_amount": str(sf.target_amount),
            "target_date": sf.target_date.isoformat(),
            "contribution_strategy": sf.contribution_strategy.value,
            "contribution_amount": (
                str(sf.contribution_amount) if sf.contribution_amount is not None else None
            ),
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    return _to_read(pool, sf)


@router.delete(
    "/{sinking_fund_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("sinking_fund.not_found"),
    },
)
def deactivate_sinking_fund(
    sinking_fund_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Soft-delete (deactivate) a sinking fund. Admin only."""
    found = SinkingFundRepository(session, claims.household_id).get(sinking_fund_id)
    if found is None:
        raise SinkingFundNotFoundError()
    pool, _sf = found

    pool_repo = AllocationPoolRepository(session, claims.household_id)
    try:
        pool_repo.deactivate(sinking_fund_id)
    except LookupError as exc:
        raise SinkingFundNotFoundError() from exc

    AuditLogWriter(session, claims.household_id).write(
        action="delete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="sinking_fund",
        entity_id=pool.id,
        before={"is_active": True},
        after={"is_active": False},
        request_id=_request_uuid(request),
    )
    session.commit()


@router.get(
    "/{sinking_fund_id}/balance",
    response_model=PoolBalanceRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("sinking_fund.not_found"),
    },
)
def get_sinking_fund_balance(
    sinking_fund_id: UUID,
    as_of: date_type | None = Query(  # noqa: B008
        default=None,
        description=(
            "Optional point-in-time date (YYYY-MM-DD). Includes only "
            "shadow transactions on or before this date. Defaults to today."
        ),
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> PoolBalanceRead:
    """Return the sinking fund's derived balance (sum of POSTED shadow postings)."""
    found = SinkingFundRepository(session, claims.household_id).get(sinking_fund_id)
    if found is None:
        raise SinkingFundNotFoundError()
    pool, _sf = found
    if not filter_for_role(pool, claims):
        raise SinkingFundNotFoundError()

    effective_as_of = as_of or date_type.today()
    raw = ShadowTransactionRepository(session, claims.household_id).balance_for_pool(
        pool.id, currency=pool.currency, as_of=effective_as_of
    )
    raw_balance = raw.get(pool.currency, Decimal(0))
    balance = Money(raw_balance, pool.currency).quantize_to_currency().amount
    return PoolBalanceRead(
        pool_id=pool.id,
        name=pool.name,
        currency=pool.currency,
        balance=balance,
        as_of=effective_as_of,
    )


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None
