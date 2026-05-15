"""POST/GET/DELETE /v1/reconciliations + state-transition endpoints (P5.4.b).

Per ADR-0004 §Q4-Q9 + the locked P5.4.b scoping decisions:

- One IN_PROGRESS reconciliation per ``account_id`` at any time.
- ``source_import_batch_id`` is set at create time; auto-match takes no body.
- Re-running auto-match on a reconciliation with existing matches → 409.
- ``GET /v1/reconciliations/{id}`` returns envelope + inline inbox
  (matches + unmatched statement lines + unmatched ledger transactions
  in the period window) so the review pane is one round-trip.
- ``DELETE`` requires explicit ``?cascade=true``.
- ``/complete`` enforces strict balance: ``sum(matches) == ending - starting``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_api.auth.deps import require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    AccountNotFoundError,
    ImportBatchNotFoundError,
    ReconciliationAccountAlreadyInProgressError,
    ReconciliationCurrencyMismatchError,
    ReconciliationInvalidStateError,
    ReconciliationLineAlreadyMatchedError,
    ReconciliationLineAmountMismatchError,
    ReconciliationLineNotInBatchError,
    ReconciliationMatchesExistError,
    ReconciliationMatchNotFoundError,
    ReconciliationNotFoundError,
    ReconciliationPaperMatchNotPaperReconError,
    ReconciliationTxAccountMismatchError,
    ReconciliationTxAlreadyMatchedError,
    ReconciliationTxNotFoundError,
    ReconciliationTxNotInPeriodError,
    ReconciliationUnbalancedError,
    StatementLineNotFoundError,
    problem_response,
)
from tulip_api.schemas.reconciliation import (
    AutoMatchResponse,
    CarryForwardCreate,
    CarryForwardResponse,
    CompleteResponse,
    LedgerTransactionInbox,
    ManualMatchCreate,
    MatchRead,
    PaperMatchCreate,
    ReconciliationCreate,
    ReconciliationInboxResponse,
    ReconciliationListResponse,
    ReconciliationRead,
    StatementLineInbox,
)
from tulip_api.services.reconciliation_match import (
    AutoMatchAlreadyRunError,
    AutoMatchInvalidStateError,
    CarryForwardTxNotFoundError,
    CarryForwardTxNotInPeriodError,
    CompleteInvalidStateError,
    CompleteUnbalancedError,
    ManualMatchAmountMismatchError,
    ManualMatchLineAlreadyMatchedError,
    ManualMatchLineNotFoundError,
    ManualMatchLineNotInBatchError,
    ManualMatchTxAccountMismatchError,
    ManualMatchTxNotFoundError,
    PaperMatchNotPaperReconError,
    PaperMatchTxAlreadyMatchedError,
    PaperMatchTxNotInPeriodError,
    add_carry_forward,
    auto_match,
    complete,
    manual_match,
    paper_match,
    remove_carry_forward,
)
from tulip_storage.models import ReconciliationStatus
from tulip_storage.repositories import (
    AccountRepository,
    AuditLogWriter,
    ImportBatchRepository,
    ReconciliationMatchRepository,
    ReconciliationRepository,
    StatementLineRepository,
    TransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/reconciliations", tags=["reconciliations"])
log = structlog.get_logger("tulip_api.reconciliations")


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


def _to_read(recon: object) -> ReconciliationRead:
    return ReconciliationRead(
        id=recon.id,  # type: ignore[attr-defined]
        account_id=recon.account_id,  # type: ignore[attr-defined]
        statement_period_start=recon.statement_period_start,  # type: ignore[attr-defined]
        statement_period_end=recon.statement_period_end,  # type: ignore[attr-defined]
        statement_starting_balance=recon.statement_starting_balance,  # type: ignore[attr-defined]
        statement_ending_balance=recon.statement_ending_balance,  # type: ignore[attr-defined]
        currency=recon.currency,  # type: ignore[attr-defined]
        status=recon.status.value,  # type: ignore[attr-defined]
        source_import_batch_id=recon.source_import_batch_id,  # type: ignore[attr-defined]
        created_at=recon.created_at,  # type: ignore[attr-defined]
        completed_at=recon.completed_at,  # type: ignore[attr-defined]
    )


def _to_match_read(match: object) -> MatchRead:
    return MatchRead(
        id=match.id,  # type: ignore[attr-defined]
        reconciliation_id=match.reconciliation_id,  # type: ignore[attr-defined]
        statement_line_id=match.statement_line_id,  # type: ignore[attr-defined]
        ledger_transaction_id=match.ledger_transaction_id,  # type: ignore[attr-defined]
        match_amount=match.match_amount,  # type: ignore[attr-defined]
        currency=match.currency,  # type: ignore[attr-defined]
        confidence=(
            match.confidence.value if match.confidence is not None else None  # type: ignore[attr-defined]
        ),
        matcher_version=match.matcher_version,  # type: ignore[attr-defined]
        created_by_user_id=match.created_by_user_id,  # type: ignore[attr-defined]
        created_at=match.created_at,  # type: ignore[attr-defined]
    )


@router.get(
    "",
    response_model=ReconciliationListResponse,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def list_reconciliations(
    account_id: UUID | None = None,
    status: ReconciliationStatus | None = None,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ReconciliationListResponse:
    """List reconciliations in this household, newest statement period first."""
    rows = ReconciliationRepository(session, claims.household_id).list_for_household(
        account_id=account_id, status=status
    )
    return ReconciliationListResponse(items=[_to_read(r) for r in rows])


@router.post(
    "",
    response_model=ReconciliationRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "request.body_invalid",
            "reconciliation.currency_mismatch",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("account.not_found", "import_batch.not_found"),
        409: problem_response("reconciliation.account_already_in_progress"),
        422: problem_response("validation.failed"),
    },
)
def create_reconciliation(
    body: ReconciliationCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ReconciliationRead:
    """Open a reconciliation envelope.

    Tied to one import batch in the OFX-driven flow; ``source_import_batch_id``
    is optional in the paper-statement flow (#275) where the user ticks off
    ledger transactions against a physical statement.
    """
    accounts = AccountRepository(session, claims.household_id)
    account = accounts.get(body.account_id)
    if account is None:
        raise AccountNotFoundError()

    if body.source_import_batch_id is not None:
        batches = ImportBatchRepository(session, claims.household_id)
        batch = batches.get(body.source_import_batch_id)
        if batch is None or batch.account_id != body.account_id:
            raise ImportBatchNotFoundError()

    if account.currency != body.currency:
        raise ReconciliationCurrencyMismatchError(
            reconciliation_currency=body.currency,
            source_currency=account.currency,
            source="account",
        )

    repo = ReconciliationRepository(session, claims.household_id)
    existing = [
        r
        for r in repo.list_for_account(body.account_id)
        if r.status is ReconciliationStatus.IN_PROGRESS
    ]
    if existing:
        raise ReconciliationAccountAlreadyInProgressError(
            account_id=str(body.account_id),
            existing_reconciliation_id=str(existing[0].id),
        )

    recon = repo.create(
        account_id=body.account_id,
        statement_period_start=body.statement_period_start,
        statement_period_end=body.statement_period_end,
        statement_starting_balance=body.statement_starting_balance,
        statement_ending_balance=body.statement_ending_balance,
        currency=body.currency,
        source_import_batch_id=body.source_import_batch_id,
        created_by_user_id=claims.user_id,
    )
    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation",
        entity_id=recon.id,
        after={
            "account_id": str(body.account_id),
            "source_import_batch_id": (
                str(body.source_import_batch_id)
                if body.source_import_batch_id is not None
                else None
            ),
            "period": (f"{body.statement_period_start}..{body.statement_period_end}"),
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation.created",
        reconciliation_id=str(recon.id),
        paper=body.source_import_batch_id is None,
    )
    return _to_read(recon)


@router.get(
    "/{reconciliation_id}",
    response_model=ReconciliationInboxResponse,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("reconciliation.not_found"),
    },
)
def get_reconciliation(
    reconciliation_id: UUID,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ReconciliationInboxResponse:
    """Fetch the reconciliation + its review pane (matches + unmatched lines + ledger txs)."""
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    matches = ReconciliationMatchRepository(session, claims.household_id).list_for_reconciliation(
        recon.id
    )
    matched_line_ids = {m.statement_line_id for m in matches if m.statement_line_id is not None}
    matched_tx_ids = {m.ledger_transaction_id for m in matches}

    lines_repo = StatementLineRepository(session, claims.household_id)
    if recon.source_import_batch_id is not None:
        all_lines = lines_repo.list_for_batch(recon.source_import_batch_id)
    else:
        all_lines = []

    # #127: a line whose ``reconciliation_match_id`` points at a match in a
    # prior completed reconciliation has already been accounted for elsewhere.
    # Exclude it from this reconciliation's inbox so the user isn't asked to
    # re-match a line that's already taken. Storage owns the join; the
    # architecture test bans model imports outside repositories.
    candidate_match_ids = {
        line.reconciliation_match_id for line in all_lines if line.reconciliation_match_id
    }
    prior_completed_match_ids = ReconciliationMatchRepository(
        session, claims.household_id
    ).filter_to_completed_recons(candidate_match_ids)

    unmatched_lines = [
        line
        for line in all_lines
        if not line.is_excluded
        and line.id not in matched_line_ids
        and line.reconciliation_match_id not in prior_completed_match_ids
    ]

    tx_repo = TransactionRepository(session, claims.household_id)
    from tulip_storage.models import TransactionStatus

    headers = tx_repo.list_headers(
        account_id=recon.account_id,
        from_date=recon.statement_period_start,
        to_date=recon.statement_period_end,
        status=TransactionStatus.POSTED,
    )
    unmatched_txs = [
        h for h in headers if h.id not in matched_tx_ids and h.reconciliation_id is None
    ]

    return ReconciliationInboxResponse(
        reconciliation=_to_read(recon),
        matches=[_to_match_read(m) for m in matches],
        unmatched_statement_lines=[
            StatementLineInbox(
                id=line.id,
                line_number=line.line_number,
                posted_date=line.posted_date,
                amount=line.amount,
                currency=line.currency,
                description=line.description,
                counterparty=line.counterparty,
                reference=line.reference,
                fitid=line.fitid,
            )
            for line in unmatched_lines
        ],
        unmatched_ledger_transactions=[
            LedgerTransactionInbox(
                id=h.id,
                date=h.date,
                description=h.description,
                reference=h.reference,
                status=h.status.value,
            )
            for h in unmatched_txs
        ],
    )


@router.delete(
    "/{reconciliation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: problem_response("reconciliation.cascade_required"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("reconciliation.not_found"),
    },
)
def revert_reconciliation(
    reconciliation_id: UUID,
    request: Request,
    cascade: bool = False,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Un-reconcile: requires ``?cascade=true`` so the destructive intent is explicit.

    Per ADR-0004 §Q7. Cascades to ``reconciliation_matches`` (FK), nulls
    ``transactions.reconciliation_id`` + ``reconciled_at``, and clears
    ``statement_lines.reconciliation_match_id``.
    """
    if not cascade:
        from tulip_api.errors import ReconciliationCascadeRequiredError

        raise ReconciliationCascadeRequiredError()

    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    repo.revert(reconciliation_id)
    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_revert",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation",
        entity_id=reconciliation_id,
        before={
            "status": recon.status.value,
            "account_id": str(recon.account_id),
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("reconciliation.reverted", reconciliation_id=str(reconciliation_id))


@router.post(
    "/{reconciliation_id}/auto-match",
    response_model=AutoMatchResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("reconciliation.not_found"),
        409: problem_response(
            "reconciliation.invalid_state",
            "reconciliation.matches_exist",
        ),
    },
)
async def auto_match_endpoint(
    reconciliation_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AutoMatchResponse:
    """Run the matcher; persist candidate match rows."""
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    try:
        result = await auto_match(
            session=session,
            household_id=claims.household_id,
            reconciliation=recon,
            actor_user_id=claims.user_id,
        )
    except AutoMatchInvalidStateError as exc:
        raise ReconciliationInvalidStateError(
            current_status=exc.current_status, action="auto-match"
        ) from exc
    except AutoMatchAlreadyRunError as exc:
        raise ReconciliationMatchesExistError(
            reconciliation_id=str(reconciliation_id),
            existing_match_count=exc.existing_match_count,
        ) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_auto_match",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation",
        entity_id=recon.id,
        after={
            "matches_created": result.matches_created,
            "high": result.high_count,
            "medium": result.medium_count,
            "low": result.low_count,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation.auto_matched",
        reconciliation_id=str(recon.id),
        matches_created=result.matches_created,
    )
    return AutoMatchResponse(
        reconciliation_id=recon.id,
        matches_created=result.matches_created,
        candidate_summary={
            "high": result.high_count,
            "medium": result.medium_count,
            "low": result.low_count,
        },
    )


@router.post(
    "/{reconciliation_id}/matches/{match_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("reconciliation.not_found", "reconciliation_match.not_found"),
    },
)
def reject_match(
    reconciliation_id: UUID,
    match_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Delete a match row, returning the line + transaction to the unmatched pool."""
    recon_repo = ReconciliationRepository(session, claims.household_id)
    if recon_repo.get(reconciliation_id) is None:
        raise ReconciliationNotFoundError()

    match_repo = ReconciliationMatchRepository(session, claims.household_id)
    match = match_repo.get(match_id)
    if match is None or match.reconciliation_id != reconciliation_id:
        raise ReconciliationMatchNotFoundError()

    match_repo.reject(match_id)
    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_match_reject",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation_match",
        entity_id=match_id,
        before={
            "reconciliation_id": str(reconciliation_id),
            "statement_line_id": str(match.statement_line_id),
            "ledger_transaction_id": str(match.ledger_transaction_id),
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation_match.rejected",
        match_id=str(match_id),
        reconciliation_id=str(reconciliation_id),
    )


@router.post(
    "/{reconciliation_id}/complete",
    response_model=CompleteResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("reconciliation.not_found"),
        409: problem_response(
            "reconciliation.invalid_state",
            "reconciliation.unbalanced",
        ),
    },
)
def complete_endpoint(
    reconciliation_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> CompleteResponse:
    """Finalise the reconciliation; denormalise ``reconciled_at`` onto matched txs."""
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    try:
        result = complete(
            session=session,
            household_id=claims.household_id,
            reconciliation=recon,
        )
    except CompleteInvalidStateError as exc:
        raise ReconciliationInvalidStateError(
            current_status=exc.current_status, action="complete"
        ) from exc
    except CompleteUnbalancedError as exc:
        raise ReconciliationUnbalancedError(
            reconciliation_id=str(reconciliation_id),
            expected_net=str(exc.expected_net),
            matched_net=str(exc.matched_net),
            residual=str(exc.residual),
        ) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_complete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation",
        entity_id=recon.id,
        after={"affected_transaction_count": result.affected_transaction_count},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation.completed",
        reconciliation_id=str(recon.id),
        affected=result.affected_transaction_count,
    )
    refreshed = repo.get(recon.id)
    assert refreshed is not None  # noqa: S101 — just completed
    return CompleteResponse(
        reconciliation_id=refreshed.id,
        status=refreshed.status.value,
        completed_at=refreshed.completed_at,  # type: ignore[arg-type]
        affected_transaction_count=result.affected_transaction_count,
    )


@router.post(
    "/{reconciliation_id}/matches",
    response_model=MatchRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "request.body_invalid",
            "reconciliation.line_not_in_batch",
            "reconciliation.line_amount_mismatch",
            "reconciliation.tx_account_mismatch",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response(
            "reconciliation.not_found",
            "import.line.not_found",
            "reconciliation.transaction_not_found",
        ),
        409: problem_response(
            "reconciliation.invalid_state",
            "reconciliation.line_already_matched",
        ),
        422: problem_response("validation.failed"),
    },
)
async def create_manual_match(
    reconciliation_id: UUID,
    body: ManualMatchCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MatchRead:
    """Create a manual match — used when auto-match missed a pairing.

    Per ADR §Q9: manual matches set ``created_by_user_id``, leave
    ``confidence`` and ``matcher_version`` NULL.
    """
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    try:
        match = await manual_match(
            session=session,
            household_id=claims.household_id,
            reconciliation=recon,
            statement_line_id=body.statement_line_id,
            ledger_transaction_id=body.ledger_transaction_id,
            match_amount=body.match_amount,
            currency=body.currency,
            actor_user_id=claims.user_id,
        )
    except CompleteInvalidStateError as exc:
        raise ReconciliationInvalidStateError(
            current_status=exc.current_status, action="create-manual-match"
        ) from exc
    except ManualMatchLineNotFoundError as exc:
        raise StatementLineNotFoundError() from exc
    except ManualMatchLineNotInBatchError as exc:
        raise ReconciliationLineNotInBatchError(
            statement_line_id=str(exc.statement_line_id),
            expected_batch_id=str(exc.expected_batch_id),
        ) from exc
    except ManualMatchLineAlreadyMatchedError as exc:
        raise ReconciliationLineAlreadyMatchedError(
            statement_line_id=str(exc.statement_line_id),
            existing_match_id=str(exc.existing_match_id),
        ) from exc
    except ManualMatchAmountMismatchError as exc:
        raise ReconciliationLineAmountMismatchError(
            statement_line_id=str(exc.statement_line_id),
            line_amount=str(exc.line_amount),
            match_amount=str(exc.match_amount),
        ) from exc
    except ManualMatchTxNotFoundError as exc:
        raise ReconciliationTxNotFoundError() from exc
    except ManualMatchTxAccountMismatchError as exc:
        raise ReconciliationTxAccountMismatchError(
            ledger_transaction_id=str(exc.ledger_transaction_id),
            expected_account_id=str(exc.expected_account_id),
        ) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_match_create_manual",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation_match",
        entity_id=match.id,
        after={
            "reconciliation_id": str(reconciliation_id),
            "statement_line_id": str(body.statement_line_id),
            "ledger_transaction_id": str(body.ledger_transaction_id),
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation_match.manual_created",
        match_id=str(match.id),
        reconciliation_id=str(reconciliation_id),
    )
    return _to_match_read(match)


@router.post(
    "/{reconciliation_id}/paper-matches",
    response_model=MatchRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "request.body_invalid",
            "reconciliation.paper_match_not_paper_recon",
            "reconciliation.tx_account_mismatch",
            "reconciliation.tx_not_in_period",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response(
            "reconciliation.not_found",
            "reconciliation.transaction_not_found",
        ),
        409: problem_response(
            "reconciliation.invalid_state",
            "reconciliation.tx_already_matched",
        ),
        422: problem_response("validation.failed"),
    },
)
async def create_paper_match(
    reconciliation_id: UUID,
    body: PaperMatchCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MatchRead:
    """Mark a ledger tx as matched in a paper-statement reconciliation (#275).

    No statement_line — the user is asserting "this tx matches a line
    on my paper statement" without an imported batch to point at. The
    match amount is derived server-side from the bank-side posting on
    the recon's account.
    """
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    try:
        match = await paper_match(
            session=session,
            household_id=claims.household_id,
            reconciliation=recon,
            ledger_transaction_id=body.ledger_transaction_id,
            actor_user_id=claims.user_id,
        )
    except CompleteInvalidStateError as exc:
        raise ReconciliationInvalidStateError(
            current_status=exc.current_status, action="create-paper-match"
        ) from exc
    except PaperMatchNotPaperReconError as exc:
        raise ReconciliationPaperMatchNotPaperReconError(
            reconciliation_id=str(exc.reconciliation_id),
        ) from exc
    except ManualMatchTxNotFoundError as exc:
        raise ReconciliationTxNotFoundError() from exc
    except PaperMatchTxNotInPeriodError as exc:
        raise ReconciliationTxNotInPeriodError(
            ledger_transaction_id=str(exc.ledger_transaction_id),
            tx_date=exc.tx_date,
            period_start=exc.period_start,
            period_end=exc.period_end,
        ) from exc
    except ManualMatchTxAccountMismatchError as exc:
        raise ReconciliationTxAccountMismatchError(
            ledger_transaction_id=str(exc.ledger_transaction_id),
            expected_account_id=str(exc.expected_account_id),
        ) from exc
    except PaperMatchTxAlreadyMatchedError as exc:
        raise ReconciliationTxAlreadyMatchedError(
            ledger_transaction_id=str(exc.ledger_transaction_id),
            existing_match_id=str(exc.existing_match_id),
        ) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_match_create_paper",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation_match",
        entity_id=match.id,
        after={
            "reconciliation_id": str(reconciliation_id),
            "ledger_transaction_id": str(body.ledger_transaction_id),
            "match_amount": str(match.match_amount),
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation_match.paper_created",
        match_id=str(match.id),
        reconciliation_id=str(reconciliation_id),
    )
    return _to_match_read(match)


@router.post(
    "/{reconciliation_id}/carry-forward",
    response_model=CarryForwardResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "request.body_invalid",
            "reconciliation.tx_not_in_period",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response(
            "reconciliation.not_found",
            "reconciliation.transaction_not_found",
        ),
        409: problem_response("reconciliation.invalid_state"),
        422: problem_response("validation.failed"),
    },
)
def add_carry_forward_endpoint(
    reconciliation_id: UUID,
    body: CarryForwardCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> CarryForwardResponse:
    """Mark in-period ledger transactions as carry-forward to the next reconciliation."""
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    try:
        added = add_carry_forward(
            session=session,
            household_id=claims.household_id,
            reconciliation=recon,
            transaction_ids=body.transaction_ids,
        )
    except CompleteInvalidStateError as exc:
        raise ReconciliationInvalidStateError(
            current_status=exc.current_status, action="add-carry-forward"
        ) from exc
    except CarryForwardTxNotFoundError as exc:
        raise ReconciliationTxNotFoundError() from exc
    except CarryForwardTxNotInPeriodError as exc:
        raise ReconciliationTxNotInPeriodError(
            ledger_transaction_id=str(exc.ledger_transaction_id),
            tx_date=exc.tx_date,
            period_start=exc.period_start,
            period_end=exc.period_end,
        ) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_carry_forward_add",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation",
        entity_id=reconciliation_id,
        after={"transaction_ids": [str(t) for t in added]},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation.carry_forward_added",
        reconciliation_id=str(reconciliation_id),
        count=len(added),
    )
    return CarryForwardResponse(reconciliation_id=reconciliation_id, transaction_ids=added)


@router.delete(
    "/{reconciliation_id}/carry-forward/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response(
            "reconciliation.not_found",
            "reconciliation.transaction_not_found",
        ),
        409: problem_response("reconciliation.invalid_state"),
    },
)
def remove_carry_forward_endpoint(
    reconciliation_id: UUID,
    transaction_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Un-mark a transaction's carry-forward link."""
    repo = ReconciliationRepository(session, claims.household_id)
    recon = repo.get(reconciliation_id)
    if recon is None:
        raise ReconciliationNotFoundError()

    try:
        remove_carry_forward(
            session=session,
            household_id=claims.household_id,
            reconciliation=recon,
            transaction_id=transaction_id,
        )
    except CompleteInvalidStateError as exc:
        raise ReconciliationInvalidStateError(
            current_status=exc.current_status, action="remove-carry-forward"
        ) from exc
    except CarryForwardTxNotFoundError as exc:
        raise ReconciliationTxNotFoundError() from exc

    AuditLogWriter(session, claims.household_id).write(
        action="reconciliation_carry_forward_remove",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="reconciliation",
        entity_id=reconciliation_id,
        before={"transaction_id": str(transaction_id)},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "reconciliation.carry_forward_removed",
        reconciliation_id=str(reconciliation_id),
        transaction_id=str(transaction_id),
    )
