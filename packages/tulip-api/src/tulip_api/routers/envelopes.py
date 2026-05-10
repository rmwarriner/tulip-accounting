"""GET / POST / PATCH / DELETE / refill / balance for /v1/envelopes.

Per ADR-0001. Envelopes are a flavor of allocation pool; this router exposes
CRUD + a refill action. Balance is a separate sub-route mirroring the
accounts pattern. The refill endpoint constructs a stand-alone shadow
transaction (Unallocated -X / envelope +X) — see
:mod:`tulip_api.routers._pool_helpers`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    EnvelopeNotFoundError,
    ForbiddenError,
    problem_response,
)
from tulip_api.routers._pool_helpers import (
    filter_for_role,
    post_user_initiated_shadow_tx,
    require_visibility_or_forbid,
    resolve_or_lazy_create_system_pool,
)
from tulip_api.schemas.envelope import (
    EnvelopeCreate,
    EnvelopeRead,
    EnvelopeUpdate,
    RefillRequest,
    RefillRuleSchema,
)
from tulip_api.schemas.pool import PoolBalanceRead
from tulip_core.allocation import (
    PoolType as DomainPoolType,
)
from tulip_core.allocation import (
    RefillRule,
    ShadowTxReason,
)
from tulip_core.money import Money
from tulip_storage.models import (
    AllocationPool,
    BudgetPeriod,
    Envelope,
    RolloverPolicy,
)
from tulip_storage.repositories import (
    AllocationPoolRepository,
    AuditLogWriter,
    EnvelopeRepository,
    ShadowTransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/envelopes", tags=["envelopes"])
log = structlog.get_logger("tulip_api.envelopes")


def _to_read(pool: AllocationPool, env: Envelope) -> EnvelopeRead:
    """Build an EnvelopeRead from the joined ``(pool, envelope)`` rows."""
    refill_rule_schema: RefillRuleSchema | None = None
    if env.refill_rule_json is not None:
        rule_dict = json.loads(env.refill_rule_json)
        # Round-trip via the domain RefillRule so the no-eval guarantee is
        # uniform — even if the JSON in the column was mutated outside the
        # API layer, the response shape conforms.
        rule = RefillRule.from_dict(rule_dict)
        refill_rule_schema = RefillRuleSchema.model_validate(rule.to_dict())
    return EnvelopeRead(
        id=pool.id,
        name=pool.name,
        currency=pool.currency,
        visibility=pool.visibility,
        is_active=pool.is_active,
        budget_period=env.budget_period.value,
        rollover_policy=env.rollover_policy.value,
        budget_amount=env.budget_amount,
        refill_rule=refill_rule_schema,
    )


def _validate_refill_rule_or_raise(rule_schema: RefillRuleSchema, currency: str) -> None:
    """Construct a domain ``RefillRule`` to surface field-level errors at the API.

    Pydantic's gross-shape check has already passed; the domain object's
    ``__post_init__`` carries the per-strategy semantics (positive amounts,
    percentage in (0, 1], strategy / field combo). Any ``ValueError`` here
    bubbles up as a Problem Details ``request.body_invalid`` (400) via the
    framework wrapper.
    """
    rule_dict = rule_schema.model_dump(exclude_none=True)
    # Default the currency to the envelope's if the schema omitted it.
    rule_dict.setdefault("currency", currency)
    RefillRule.from_dict(rule_dict)


@router.get(
    "",
    response_model=list[EnvelopeRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_envelopes(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[EnvelopeRead]:
    """List active envelopes visible to the caller."""
    repo = EnvelopeRepository(session, claims.household_id)
    rows = [(p, e) for p, e in repo.list_active() if filter_for_role(p, claims)]
    return [_to_read(p, e) for p, e in rows]


@router.post(
    "",
    response_model=EnvelopeRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def create_envelope(
    body: EnvelopeCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> EnvelopeRead:
    """Create a new envelope in the caller's household."""
    refill_rule_dict: dict[str, str] | None = None
    if body.refill_rule is not None:
        _validate_refill_rule_or_raise(body.refill_rule, body.currency)
        # Round-trip through the domain object so Decimal/Currency become
        # JSON-safe strings (RefillRule.to_dict serializes Decimals to str).
        rule_dict = body.refill_rule.model_dump(exclude_none=True)
        rule_dict.setdefault("currency", body.currency)
        refill_rule_dict = RefillRule.from_dict(rule_dict).to_dict()

    pool, env = EnvelopeRepository(session, claims.household_id).create(
        name=body.name,
        currency=body.currency,
        budget_period=BudgetPeriod(body.budget_period),
        rollover_policy=RolloverPolicy(body.rollover_policy),
        budget_amount=body.budget_amount,
        refill_rule=refill_rule_dict,
        visibility=body.visibility,
        created_by_user_id=claims.user_id,
    )
    AuditLogWriter(session, claims.household_id).write(
        action="create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="envelope",
        entity_id=pool.id,
        after={
            "name": pool.name,
            "currency": pool.currency,
            "visibility": pool.visibility,
            "budget_period": env.budget_period.value,
            "rollover_policy": env.rollover_policy.value,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("envelope.created", envelope_id=str(pool.id))
    return _to_read(pool, env)


@router.get(
    "/{envelope_id}",
    response_model=EnvelopeRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("envelope.not_found"),
    },
)
def get_envelope(
    envelope_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> EnvelopeRead:
    """Fetch an envelope by id (404 if not in household or not visible)."""
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()
    return _to_read(pool, env)


@router.patch(
    "/{envelope_id}",
    response_model=EnvelopeRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("envelope.not_found"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def update_envelope(
    envelope_id: UUID,
    body: EnvelopeUpdate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> EnvelopeRead:
    """Update mutable fields. Member cannot edit private envelopes they didn't create."""
    repo = EnvelopeRepository(session, claims.household_id)
    found = repo.get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()
    if (
        claims.role == "member"
        and pool.visibility == "private"
        and pool.created_by_user_id != claims.user_id
    ):
        raise ForbiddenError(
            "Members can only edit private envelopes they created themselves. "
            "Ask an admin, or have the original creator make the change."
        )

    if body.refill_rule is not None:
        _validate_refill_rule_or_raise(body.refill_rule, pool.currency)

    before = {
        "name": pool.name,
        "visibility": pool.visibility,
        "budget_period": env.budget_period.value,
        "budget_amount": str(env.budget_amount) if env.budget_amount is not None else None,
        "rollover_policy": env.rollover_policy.value,
        "refill_rule_present": env.refill_rule_json is not None,
    }

    refill_rule_dict: dict[str, str] | None = None
    if body.refill_rule is not None:
        rule_dict = body.refill_rule.model_dump(exclude_none=True)
        rule_dict.setdefault("currency", pool.currency)
        refill_rule_dict = RefillRule.from_dict(rule_dict).to_dict()

    pool, env = repo.update_fields(
        envelope_id,
        name=body.name,
        visibility=body.visibility,
        budget_period=BudgetPeriod(body.budget_period) if body.budget_period else None,
        budget_amount=body.budget_amount,
        rollover_policy=(RolloverPolicy(body.rollover_policy) if body.rollover_policy else None),
        refill_rule=refill_rule_dict,
    )

    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="envelope",
        entity_id=pool.id,
        before=before,
        after={
            "name": pool.name,
            "visibility": pool.visibility,
            "budget_period": env.budget_period.value,
            "budget_amount": str(env.budget_amount) if env.budget_amount is not None else None,
            "rollover_policy": env.rollover_policy.value,
            "refill_rule_present": env.refill_rule_json is not None,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    return _to_read(pool, env)


@router.delete(
    "/{envelope_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("envelope.not_found"),
    },
)
def deactivate_envelope(
    envelope_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Soft-delete (deactivate) an envelope. Admin only."""
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, _env = found

    pool_repo = AllocationPoolRepository(session, claims.household_id)
    try:
        pool_repo.deactivate(envelope_id)
    except LookupError as exc:
        raise EnvelopeNotFoundError() from exc

    AuditLogWriter(session, claims.household_id).write(
        action="delete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="envelope",
        entity_id=pool.id,
        before={"is_active": True},
        after={"is_active": False},
        request_id=_request_uuid(request),
    )
    session.commit()


@router.get(
    "/{envelope_id}/balance",
    response_model=PoolBalanceRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("envelope.not_found"),
    },
)
def get_envelope_balance(
    envelope_id: UUID,
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
    """Return the envelope's derived balance (sum of POSTED shadow postings)."""
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, _env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()

    effective_as_of = as_of or datetime.now(UTC).date()
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


@router.post(
    "/{envelope_id}/refill",
    response_model=PoolBalanceRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("envelope.not_found"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def refill_envelope(
    envelope_id: UUID,
    body: RefillRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> PoolBalanceRead:
    """Refill an envelope from the household's Unallocated system pool.

    Posts a 2-leg shadow transaction (``Unallocated -X`` / envelope ``+X``)
    with reason ``REFILL``. The Unallocated pool for the envelope's
    currency is lazy-created if missing.
    """
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, _env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()
    require_visibility_or_forbid(pool, claims)

    pool_repo = AllocationPoolRepository(session, claims.household_id)
    unallocated = resolve_or_lazy_create_system_pool(
        pool_repo,
        pool_type=DomainPoolType.UNALLOCATED,
        currency=pool.currency,
    )

    post_user_initiated_shadow_tx(
        session=session,
        claims=claims,
        request_id=_request_uuid(request),
        description=body.description,
        reason=ShadowTxReason.REFILL,
        tx_date=body.date,
        legs=[
            (unallocated, -body.amount),
            (pool, body.amount),
        ],
        memo=body.memo,
    )

    session.commit()

    # Return the envelope's new balance for ergonomics. UTC to match
    # the runner's clock (#141) — the user-supplied body.date is also
    # interpreted in UTC by the shadow ledger.
    effective_as_of = datetime.now(UTC).date()
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
