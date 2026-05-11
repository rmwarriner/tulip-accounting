"""Pool-level action endpoints: transfer + budget-inflow.

Per ADR-0001:

- ``POST /v1/pools/{src_pool_id}/transfer`` moves funds between two
  user pools (envelope or sinking_fund), same currency. Source and
  destination must both be active and visible to the caller.
- ``POST /v1/pools/budget-inflow`` declares ``amount`` of new money
  available to budget; lazy-creates ``Inflow`` + ``Unallocated`` system
  pools for the currency if missing.

Both build a stand-alone shadow transaction (``paired_main_tx_id=None``)
via :func:`tulip_api.routers._pool_helpers.post_user_initiated_shadow_tx`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    PoolInflowCurrencyUnknownError,
    PoolNotFoundError,
    PoolTransferCurrencyMismatchError,
    PoolTransferSamePoolError,
    PoolTransferSystemPoolForbiddenError,
    problem_response,
)
from tulip_api.routers._pool_helpers import (
    filter_for_role,
    post_user_initiated_shadow_tx,
    require_visibility_or_forbid,
)
from tulip_api.schemas.pool import (
    BudgetInflowRequest,
    PoolBalanceRead,
    PoolBalancesRequest,
    TransferRequest,
)
from tulip_core.allocation import (
    ShadowTxReason,
)
from tulip_core.currency import Currency
from tulip_core.money import Money
from tulip_storage.models import PoolType
from tulip_storage.repositories import (
    AllocationPoolRepository,
    ShadowTransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/pools", tags=["pools"])
log = structlog.get_logger("tulip_api.pools")


@router.post(
    "/{src_pool_id}/transfer",
    response_model=PoolBalanceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        400: problem_response(
            "pool.not_found",
            "pool.inactive",
            "pool.transfer_same_pool",
            "pool.transfer_currency_mismatch",
            "pool.transfer_system_pool_forbidden",
            "request.body_invalid",
        ),
        422: problem_response("validation.failed"),
    },
)
def transfer_between_pools(
    src_pool_id: UUID,
    body: TransferRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> PoolBalanceRead:
    """Move ``amount`` from one user pool to another.

    Both pools must be:
    - In the caller's household.
    - Active.
    - Visible to the caller (private pools require creator-or-admin).
    - Of type ``envelope`` or ``sinking_fund`` (system pools rejected).
    - Of the same currency.
    """
    if src_pool_id == body.dest_pool_id:
        raise PoolTransferSamePoolError()

    pool_repo = AllocationPoolRepository(session, claims.household_id)
    src = pool_repo.get(src_pool_id)
    dest = pool_repo.get(body.dest_pool_id)
    if src is None:
        raise PoolNotFoundError(pool_id=str(src_pool_id))
    if dest is None:
        raise PoolNotFoundError(pool_id=str(body.dest_pool_id))

    # Visibility — surface as 404 if invisible, never as 403 (don't leak
    # the existence of pools the caller can't see).
    if not filter_for_role(src, claims):
        raise PoolNotFoundError(pool_id=str(src_pool_id))
    if not filter_for_role(dest, claims):
        raise PoolNotFoundError(pool_id=str(body.dest_pool_id))

    # System-pool guard. Surfaces a single error code with role extension.
    if src.is_system:
        raise PoolTransferSystemPoolForbiddenError(role="source")
    if dest.is_system:
        raise PoolTransferSystemPoolForbiddenError(role="destination")

    if not src.is_active:
        from tulip_api.errors import PoolInactiveError

        raise PoolInactiveError(pool_id=str(src.id))
    if not dest.is_active:
        from tulip_api.errors import PoolInactiveError

        raise PoolInactiveError(pool_id=str(dest.id))

    # Member-can't-act-on-private-they-don't-own — same as PATCH semantics.
    require_visibility_or_forbid(src, claims)
    require_visibility_or_forbid(dest, claims)

    if src.currency != dest.currency:
        raise PoolTransferCurrencyMismatchError(
            src_currency=src.currency,
            dest_currency=dest.currency,
        )

    post_user_initiated_shadow_tx(
        session=session,
        claims=claims,
        request_id=_request_uuid(request),
        description=body.description,
        reason=ShadowTxReason.TRANSFER,
        tx_date=body.date,
        legs=[
            (src, -body.amount),
            (dest, body.amount),
        ],
        memo=body.memo,
    )
    session.commit()

    # Return the destination's new balance.
    raw = ShadowTransactionRepository(session, claims.household_id).balance_for_pool(
        dest.id, currency=dest.currency
    )
    raw_balance = raw.get(dest.currency, Decimal(0))
    balance = Money(raw_balance, dest.currency).quantize_to_currency().amount
    return PoolBalanceRead(
        pool_id=dest.id,
        name=dest.name,
        currency=dest.currency,
        balance=balance,
        as_of=body.date,
    )


@router.post(
    "/budget-inflow",
    response_model=PoolBalanceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        400: problem_response(
            "pool.inflow_currency_unknown",
            "request.body_invalid",
        ),
        422: problem_response("validation.failed"),
    },
)
def declare_budget_inflow(
    body: BudgetInflowRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> PoolBalanceRead:
    """Declare ``amount`` of new money available to budget.

    Posts a shadow transaction with reason ``budget_inflow`` of the form
    ``Inflow -X`` / ``Unallocated +X``. Lazy-creates the household's
    ``Inflow`` / ``Unallocated`` / ``Spent`` system pools for the currency
    if any are missing.
    """
    # Currency validation. Pydantic already enforced the 3-char shape; we
    # also need it to be a real ISO 4217 code (our internal whitelist).
    try:
        Currency.from_code(body.currency)
    except ValueError as exc:
        raise PoolInflowCurrencyUnknownError(currency=body.currency) from exc

    pool_repo = AllocationPoolRepository(session, claims.household_id)
    sys_pools = pool_repo.get_or_create_system_pools(currency=body.currency)
    inflow = sys_pools[PoolType.INFLOW]
    unallocated = sys_pools[PoolType.UNALLOCATED]

    post_user_initiated_shadow_tx(
        session=session,
        claims=claims,
        request_id=_request_uuid(request),
        description=body.description,
        reason=ShadowTxReason.BUDGET_INFLOW,
        tx_date=body.date,
        legs=[
            (inflow, -body.amount),
            (unallocated, body.amount),
        ],
        memo=body.memo,
    )
    session.commit()

    raw = ShadowTransactionRepository(session, claims.household_id).balance_for_pool(
        unallocated.id, currency=body.currency
    )
    raw_balance = raw.get(body.currency, Decimal(0))
    balance = Money(raw_balance, body.currency).quantize_to_currency().amount
    return PoolBalanceRead(
        pool_id=unallocated.id,
        name=unallocated.name,
        currency=body.currency,
        balance=balance,
        as_of=body.date,
    )


@router.post(
    "/balances",
    response_model=list[PoolBalanceRead],
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        422: problem_response("validation.failed"),
    },
)
def get_pool_balances(
    body: PoolBalancesRequest,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[PoolBalanceRead]:
    """Batched pool balance lookup (#137).

    Returns one ``PoolBalanceRead`` per requested pool id that exists in
    the caller's household. Foreign-tenant ids and ids that don't exist
    are silently omitted (mirrors the per-pool ``get`` semantics — 404s
    aren't useful here because the typical caller is rendering a list
    that may include rows it just learned about). Pools with no postings
    return ``balance = 0`` quantized to the pool's currency.
    """
    pool_repo = AllocationPoolRepository(session, claims.household_id)
    pools = pool_repo.list_by_ids(body.pool_ids)
    if not pools:
        return []

    shadow_repo = ShadowTransactionRepository(session, claims.household_id)
    as_of = datetime.now(UTC).date()
    balances_map = shadow_repo.balances_for_pools([p.id for p in pools], as_of=as_of)
    out: list[PoolBalanceRead] = []
    for pool in pools:
        currency_map = balances_map.get(pool.id, {})
        raw = currency_map.get(pool.currency, Decimal(0))
        balance = Money(raw, pool.currency).quantize_to_currency().amount
        out.append(
            PoolBalanceRead(
                pool_id=pool.id,
                name=pool.name,
                currency=pool.currency,
                balance=balance,
                as_of=as_of,
            )
        )
    return out


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None
