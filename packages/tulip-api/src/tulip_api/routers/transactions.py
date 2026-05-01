"""POST/GET /v1/transactions — routes through the accounting engine."""

from __future__ import annotations

from datetime import date as date_type
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    AccountUnknownError,
    PeriodClosedError,
    TransactionInvalidError,
    TransactionNotFoundError,
    TransactionUnbalancedError,
    problem_response,
)
from tulip_api.schemas.transaction import (
    PostingRead,
    TransactionCreate,
    TransactionRead,
)
from tulip_core.accounting import (
    ClosedPeriodError,
    UnbalancedTransactionError,
    post_transaction,
)
from tulip_core.money import Money
from tulip_core.periods import Period as DomainPeriod
from tulip_core.periods import PeriodStatus as DomainPS
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import TransactionStatus as StorageTxStatus
from tulip_storage.repositories import (
    AccountRepository,
    AuditLogWriter,
    PeriodRepository,
    TransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/transactions", tags=["transactions"])
log = structlog.get_logger("tulip_api.transactions")


@router.post(
    "",
    response_model=TransactionRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "account.unknown",
            "transaction.invalid",
            "transaction.unbalanced",
            "period.closed",
            "request.body_invalid",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def create_transaction(
    body: TransactionCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionRead:
    """Build a domain Transaction, post it through the engine, persist."""
    accounts_repo = AccountRepository(session, claims.household_id)
    for p in body.postings:
        if accounts_repo.get(p.account_id) is None:
            raise AccountUnknownError(account_id=str(p.account_id))

    domain_postings: tuple[DomainPosting, ...] = tuple(
        DomainPosting(
            id=uuid4(),
            account_id=p.account_id,
            amount=Money(p.amount, p.currency),
            memo=p.memo,
        )
        for p in body.postings
    )

    try:
        # Construct as PENDING so post_transaction's period check + balance
        # check both run; it promotes to POSTED on success.
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=claims.household_id,
            date=body.date,
            description=body.description,
            reference=body.reference,
            postings=domain_postings,
            status=DomainTxStatus.PENDING,
            created_by_user_id=claims.user_id,
        )
    except ValueError as exc:
        raise TransactionInvalidError(reason=str(exc)) from exc

    period_repo = PeriodRepository(session, claims.household_id)
    candidate_periods = _domain_periods(period_repo)
    try:
        posted = post_transaction(domain_tx, periods=candidate_periods)
    except UnbalancedTransactionError as exc:
        raise TransactionUnbalancedError(reason=f"Transaction does not balance: {exc}") from exc
    except ClosedPeriodError as exc:
        raise PeriodClosedError(reason=str(exc)) from exc

    tx_repo = TransactionRepository(session, claims.household_id)
    saved = tx_repo.save_balanced(posted)

    AuditLogWriter(session, claims.household_id).write(
        action="create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=saved.id,
        after={"description": saved.description, "date": saved.date.isoformat()},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("transaction.created", tx_id=str(saved.id))
    return _read_response(saved.id, claims.household_id, session)


@router.get(
    "/{tx_id}",
    response_model=TransactionRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("transaction.not_found"),
    },
)
def get_transaction(
    tx_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionRead:
    """Fetch a transaction (header + postings) by id."""
    repo = TransactionRepository(session, claims.household_id)
    if repo.get(tx_id) is None:
        raise TransactionNotFoundError()
    return _read_response(tx_id, claims.household_id, session)


@router.get(
    "",
    response_model=list[TransactionRead],
    responses={
        401: problem_response("auth.unauthorized"),
        422: problem_response("validation.failed"),
    },
)
def list_transactions(
    account_id: UUID | None = Query(  # noqa: B008
        default=None,
        description=(
            "Restrict to transactions with at least one posting on this account (any currency)."
        ),
    ),
    from_date: date_type | None = Query(  # noqa: B008
        default=None,
        alias="from",
        description="Inclusive lower bound on transaction date (YYYY-MM-DD).",
    ),
    to_date: date_type | None = Query(  # noqa: B008
        default=None,
        alias="to",
        description="Inclusive upper bound on transaction date (YYYY-MM-DD).",
    ),
    status_: str | None = Query(
        default=None,
        alias="status",
        description="One of 'pending', 'posted', 'reconciled'.",
        pattern="^(pending|posted|reconciled)$",
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=1000,
        description="Cap on rows returned (1-1000). Omit for no limit.",
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[TransactionRead]:
    """List transactions in the caller's household, newest first.

    All filter params are optional and AND together. ``account_id`` is
    a UUID. Date params use the ``from`` / ``to`` query keys (inclusive
    on both ends). ``status`` is one of the lifecycle states.
    """
    storage_status: StorageTxStatus | None = (
        StorageTxStatus(status_) if status_ is not None else None
    )
    rows = TransactionRepository(session, claims.household_id).list_headers(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        status=storage_status,
        limit=limit,
    )
    return [_read_response(t.id, claims.household_id, session) for t in rows]


# ---- helpers ---------------------------------------------------------------


def _domain_periods(repo: PeriodRepository) -> list[DomainPeriod]:
    """Return PeriodRepository's rows wrapped as core Period value objects."""
    return [
        DomainPeriod(
            id=p.id,
            household_id=p.household_id,
            start_date=p.start_date,
            end_date=p.end_date,
            status=DomainPS(p.status.value),
        )
        for p in repo.list_all()
    ]


def _read_response(tx_id: UUID, household_id: UUID, session: Session) -> TransactionRead:
    repo = TransactionRepository(session, household_id)
    header = repo.get(tx_id)
    assert header is not None  # caller verifies before invoking  # noqa: S101
    postings = repo.list_postings(tx_id)
    return TransactionRead(
        id=header.id,
        date=header.date,
        description=header.description,
        reference=header.reference,
        status=header.status.value,
        postings=[
            PostingRead(
                id=p.id,
                account_id=p.account_id,
                amount=p.amount,
                currency=p.currency,
                memo=p.memo,
            )
            for p in postings
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
