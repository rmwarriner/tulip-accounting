"""POST/GET /v1/transactions — routes through the accounting engine."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_type
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.config import get_settings
from tulip_api.deps import get_session
from tulip_api.errors import (
    FRAMEWORK_BODY_RESPONSES,
    AccountUnknownError,
    PeriodClosedError,
    PoolCurrencyMismatchError,
    PoolInactiveError,
    PoolInvalidAccountTypePairingError,
    PoolNotFoundError,
    ShadowLedgerInternalError,
    TagInvalidError,
    TransactionAlreadyVoidedError,
    TransactionInvalidError,
    TransactionNotDeletableError,
    TransactionNotEditableError,
    TransactionNotFoundError,
    TransactionNotRectifiableError,
    TransactionNotVoidableError,
    TransactionUnbalancedError,
    problem_response,
)
from tulip_api.schemas.transaction import (
    PostingRead,
    TransactionCreate,
    TransactionRead,
    TransactionRectifyRequest,
    TransactionReplaceRequest,
    TransactionReplaceResponse,
    TransactionUpdate,
    TransactionVoidRequest,
    TransactionVoidResponse,
)
from tulip_core.account import AccountType as DomainAccountType
from tulip_core.accounting import (
    ClosedPeriodError,
    UnbalancedTransactionError,
    build_reversal,
    post_transaction,
)
from tulip_core.allocation import (
    InvalidAccountTypePairingError,
    MultiCurrencyPoolTaggingError,
    UnsupportedRefundShapedShadowTxError,
    derive_paired_shadow_tx,
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
from tulip_storage.models import PoolType as StoragePoolType
from tulip_storage.models import TransactionStatus as StorageTxStatus
from tulip_storage.repositories import (
    AccountRepository,
    AllocationPoolRepository,
    AuditLogWriter,
    PeriodRepository,
    ShadowTransactionRepository,
    TransactionRepository,
    TransactionTagRepository,
)
from tulip_storage.repositories.transaction import (
    UNSET,
)
from tulip_storage.repositories.transaction import (
    TransactionNotDeletableError as RepoNotDeletableError,
)
from tulip_storage.repositories.transaction import (
    TransactionNotEditableError as RepoNotEditableError,
)
from tulip_storage.repositories.transaction import (
    TransactionNotRectifiableError as RepoNotRectifiableError,
)
from tulip_storage.repositories.transaction_tag import (
    TagInvalidError as RepoTagInvalidError,
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
            "account.placeholder_posting",
            "transaction.invalid",
            "transaction.unbalanced",
            "period.closed",
            "pool.not_found",
            "pool.inactive",
            "pool.currency_mismatch",
            "pool.invalid_account_type_pairing",
            "request.body_invalid",
            "tag.invalid",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
        500: problem_response("pool.shadow_unbalanced"),
    },
)
def create_transaction(
    body: TransactionCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionRead:
    """Build a domain Transaction, post it through the engine, persist.

    When any posting carries ``pool_id``, this handler also writes a
    paired shadow-ledger transaction in the same session per ADR-0001.
    Both ledgers commit atomically — the shadow side rolls back the main
    tx if it fails an invariant.
    """
    accounts_repo = AccountRepository(session, claims.household_id)
    accounts_by_id = {}
    for p in body.postings:
        a = accounts_repo.get(p.account_id)
        if a is None:
            raise AccountUnknownError(account_id=str(p.account_id))
        if a.is_placeholder:
            from tulip_api.errors import AccountPlaceholderPostingError

            raise AccountPlaceholderPostingError(account_id=str(p.account_id))
        accounts_by_id[p.account_id] = a

    # ---- Pool pre-flight checks ------------------------------------
    # Order: not_found → inactive → invalid_account_type → currency_mismatch.
    # All four run before any DB write so a malformed request can't
    # leave an orphan main-ledger row.
    pool_repo = AllocationPoolRepository(session, claims.household_id)
    pool_tagged_currencies: set[str] = set()
    for p in body.postings:
        if p.pool_id is None:
            continue
        pool = pool_repo.get(p.pool_id)
        if pool is None:
            raise PoolNotFoundError(pool_id=str(p.pool_id))
        if not pool.is_active:
            raise PoolInactiveError(pool_id=str(p.pool_id))
        account = accounts_by_id[p.account_id]
        if account.type.value != "expense":
            raise PoolInvalidAccountTypePairingError(account_type=account.type.value)
        if pool.currency != p.currency:
            raise PoolCurrencyMismatchError(
                pool_id=str(p.pool_id),
                pool_currency=pool.currency,
                posting_currency=p.currency,
            )
        pool_tagged_currencies.add(p.currency)

    # ---- Lazy system-pool creation ---------------------------------
    # For each currency that touches a pool-tagged posting, ensure the
    # household has its three system pools. The resolver is idempotent
    # (P4.0); calling it for an already-materialized currency is a noop.
    spent_pool_by_currency: dict[str, UUID] = {}
    for ccy in pool_tagged_currencies:
        sys_pools = pool_repo.get_or_create_system_pools(currency=ccy)
        spent_pool_by_currency[ccy] = sys_pools[StoragePoolType.SPENT].id

    # ---- Domain construction ---------------------------------------
    domain_postings: tuple[DomainPosting, ...] = tuple(
        DomainPosting(
            id=uuid4(),
            account_id=p.account_id,
            amount=Money(p.amount, p.currency),
            memo=p.memo,
            pool_id=p.pool_id,
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

    # ---- Auto-pair shadow tx ---------------------------------------
    # derive_paired_shadow_tx returns None when no posting carries pool_id.
    # InvalidAccountTypePairingError + MultiCurrencyPoolTaggingError +
    # UnsupportedRefundShapedShadowTxError all map to user-facing 400s.
    # Other engine errors (no Spent pool, balance check) are bugs → 500.
    account_types_by_id = {
        aid: DomainAccountType(a.type.value) for aid, a in accounts_by_id.items()
    }
    try:
        shadow_tx = derive_paired_shadow_tx(
            posted,
            account_types_by_id=account_types_by_id,
            spent_pool_by_currency=spent_pool_by_currency,
        )
    except InvalidAccountTypePairingError as exc:
        # Pre-flight should already have caught this, but keep the
        # mapping so the engine remains a complete contract.
        raise PoolInvalidAccountTypePairingError(
            account_type="non-expense",
        ) from exc
    except MultiCurrencyPoolTaggingError as exc:
        raise TransactionInvalidError(reason=str(exc)) from exc
    except UnsupportedRefundShapedShadowTxError as exc:
        raise TransactionInvalidError(reason=str(exc)) from exc
    except ValueError as exc:
        # Engine raised a non-typed error: missing system pool, internal
        # invariant. Always a Tulip bug, not user input.
        raise ShadowLedgerInternalError() from exc

    settings = get_settings()
    tx_repo = TransactionRepository(session, claims.household_id, master_key=settings.master_key)
    saved = tx_repo.save_balanced(posted, notes=body.notes)

    paired_shadow_tx_id: UUID | None = None
    if shadow_tx is not None:
        shadow_repo = ShadowTransactionRepository(session, claims.household_id)
        saved_shadow = shadow_repo.save_balanced(shadow_tx)
        paired_shadow_tx_id = saved_shadow.id

    # #39: write tags after the transaction lands so the FK target exists.
    # Tag validation runs inside the repo; surface failures as 400 tag.invalid.
    if body.tags:
        try:
            TransactionTagRepository(session, claims.household_id).set_tags(
                saved.id, list(body.tags)
            )
        except RepoTagInvalidError as exc:
            raise TagInvalidError(reason=str(exc)) from exc

    audit_after: dict[str, str] = {
        "description": saved.description,
        "date": saved.date.isoformat(),
    }
    if paired_shadow_tx_id is not None:
        audit_after["paired_shadow_tx_id"] = str(paired_shadow_tx_id)

    AuditLogWriter(session, claims.household_id).write(
        action="create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=saved.id,
        after=audit_after,
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "transaction.created",
        tx_id=str(saved.id),
        paired_shadow_tx_id=str(paired_shadow_tx_id) if paired_shadow_tx_id else None,
    )
    return _read_response(
        saved.id,
        claims.household_id,
        session,
        paired_shadow_tx_id=paired_shadow_tx_id,
    )


@router.post(
    "/{tx_id}/void",
    response_model=TransactionVoidResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response("period.closed", "transaction.unbalanced"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("transaction.not_found"),
        409: problem_response("transaction.already_voided", "transaction.not_voidable"),
        422: problem_response("validation.failed"),
    },
)
def void_transaction(
    tx_id: UUID,
    body: TransactionVoidRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionVoidResponse:
    """Post a sibling reversal that voids ``tx_id``.

    The reversal is a normal POSTED transaction with sign-flipped postings.
    Its ``date`` (defaulting to today) is checked against open periods —
    the source's period being closed is fine because the reversal lives in
    a different (open) period (per ADR-0004 §"What P5.0 ships").

    When the source carries a paired shadow tx (ADR-0001), the shadow tx
    is auto-voided (status flip to ``voided``) in the same atomic commit.
    Pool balances auto-correct because the shadow-balance query excludes
    voided shadow txs.
    """
    tx_repo = TransactionRepository(session, claims.household_id)
    source_storage = tx_repo.get(tx_id)
    if source_storage is None:
        raise TransactionNotFoundError()
    if source_storage.voided_by_transaction_id is not None:
        raise TransactionAlreadyVoidedError(
            voided_by_transaction_id=str(source_storage.voided_by_transaction_id)
        )
    if source_storage.status not in (
        StorageTxStatus.POSTED,
        StorageTxStatus.RECONCILED,
    ):
        raise TransactionNotVoidableError(status=source_storage.status.value)

    # Reconstitute source as a domain Transaction so build_reversal can
    # sign-flip its postings cleanly.
    source_postings = tx_repo.list_postings(tx_id)
    source_domain = DomainTransaction(
        id=source_storage.id,
        household_id=source_storage.household_id,
        date=source_storage.date,
        description=source_storage.description,
        reference=source_storage.reference,
        postings=tuple(
            DomainPosting(
                id=p.id,
                account_id=p.account_id,
                amount=Money(p.amount, p.currency),
                memo=p.memo,
                pool_id=p.pool_id,
            )
            for p in source_postings
        ),
        status=DomainTxStatus(source_storage.status.value),
        created_by_user_id=source_storage.created_by_user_id,
    )

    reversal_date = body.reversal_date or date_type.today()
    reversal_pending = build_reversal(
        source_domain,
        reversal_id=uuid4(),
        reversal_date=reversal_date,
        description=f"Reversal of {source_domain.description}: {body.reason}",
        actor_user_id=claims.user_id,
    )

    period_repo = PeriodRepository(session, claims.household_id)
    candidate_periods = _domain_periods(period_repo)
    try:
        reversal_posted = post_transaction(reversal_pending, periods=candidate_periods)
    except ClosedPeriodError as exc:
        raise PeriodClosedError(reason=str(exc)) from exc
    except UnbalancedTransactionError as exc:  # defense in depth
        raise TransactionUnbalancedError(
            reason=f"Reversal failed to balance (Tulip bug): {exc}"
        ) from exc

    voided_at = datetime.now(tz=UTC)
    tx_repo.persist_reversal(tx_id, reversal_posted, voided_at=voided_at)

    # Option (c): if the source had a paired shadow tx, auto-void it in
    # the same commit. Status flip → balance_for_pool auto-corrects.
    shadow_repo = ShadowTransactionRepository(session, claims.household_id)
    paired_shadow_tx_id = shadow_repo.get_paired_id_for_main_tx(tx_id)
    if paired_shadow_tx_id is not None:
        shadow_repo.void(paired_shadow_tx_id, voided_at=voided_at)

    audit_after: dict[str, str] = {
        "reversal_id": str(reversal_posted.id),
        "reason": body.reason,
        "reversal_date": reversal_date.isoformat(),
    }
    if paired_shadow_tx_id is not None:
        audit_after["paired_shadow_tx_id_voided"] = str(paired_shadow_tx_id)
    AuditLogWriter(session, claims.household_id).write(
        action="void",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=tx_id,
        before={"voided_by_transaction_id": None},
        after=audit_after,
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "transaction.voided",
        source_id=str(tx_id),
        reversal_id=str(reversal_posted.id),
        paired_shadow_tx_id_voided=(str(paired_shadow_tx_id) if paired_shadow_tx_id else None),
    )
    return TransactionVoidResponse(
        source_id=tx_id,
        reversal_id=reversal_posted.id,
        voided_at=voided_at,
        paired_shadow_tx_id_voided=paired_shadow_tx_id,
    )


@router.post(
    "/{tx_id}/replace",
    response_model=TransactionReplaceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "account.unknown",
            "period.closed",
            "pool.not_found",
            "pool.inactive",
            "pool.currency_mismatch",
            "pool.invalid_account_type_pairing",
            "transaction.invalid",
            "transaction.unbalanced",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("transaction.not_found"),
        409: problem_response("transaction.already_voided", "transaction.not_voidable"),
        422: problem_response("validation.failed"),
    },
)
def replace_transaction(
    tx_id: UUID,
    body: TransactionReplaceRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionReplaceResponse:
    """Atomically void ``tx_id`` and create a replacement in one commit (#209a).

    The single-endpoint shape exists so the CLI's ``transactions edit``
    flow doesn't have to race a void call followed by a separate create
    (the in-between window could see the source voided but no
    replacement — exactly the inconsistency that "transparent edit"
    promises not to expose). Both writes commit together or roll back
    together.

    Pre-flight order mirrors ``/void`` then ``POST``: source must exist
    (404), must not be already voided (409 ``transaction.already_voided``),
    must be in a void-eligible status (409 ``transaction.not_voidable`` —
    i.e., POSTED or RECONCILED; a PENDING transaction should be edited
    via PATCH). The replacement's account / pool pre-flight runs before
    any DB write so a bad request never half-applies.
    """
    tx_repo = TransactionRepository(
        session, claims.household_id, master_key=get_settings().master_key
    )
    source_storage = tx_repo.get(tx_id)
    if source_storage is None:
        raise TransactionNotFoundError()
    if source_storage.voided_by_transaction_id is not None:
        raise TransactionAlreadyVoidedError(
            voided_by_transaction_id=str(source_storage.voided_by_transaction_id)
        )
    if source_storage.status not in (
        StorageTxStatus.POSTED,
        StorageTxStatus.RECONCILED,
    ):
        raise TransactionNotVoidableError(status=source_storage.status.value)

    # ---- Replacement pre-flight (account + pool) -------------------
    # Mirrors create_transaction. Runs before any write so a malformed
    # replacement can't leave the source voided + the replacement
    # missing.
    accounts_repo = AccountRepository(session, claims.household_id)
    accounts_by_id = {}
    for p in body.postings:
        a = accounts_repo.get(p.account_id)
        if a is None:
            raise AccountUnknownError(account_id=str(p.account_id))
        if a.is_placeholder:
            from tulip_api.errors import AccountPlaceholderPostingError

            raise AccountPlaceholderPostingError(account_id=str(p.account_id))
        accounts_by_id[p.account_id] = a

    pool_repo = AllocationPoolRepository(session, claims.household_id)
    pool_tagged_currencies: set[str] = set()
    for p in body.postings:
        if p.pool_id is None:
            continue
        pool = pool_repo.get(p.pool_id)
        if pool is None:
            raise PoolNotFoundError(pool_id=str(p.pool_id))
        if not pool.is_active:
            raise PoolInactiveError(pool_id=str(p.pool_id))
        account = accounts_by_id[p.account_id]
        if account.type.value != "expense":
            raise PoolInvalidAccountTypePairingError(account_type=account.type.value)
        if pool.currency != p.currency:
            raise PoolCurrencyMismatchError(
                pool_id=str(p.pool_id),
                pool_currency=pool.currency,
                posting_currency=p.currency,
            )
        pool_tagged_currencies.add(p.currency)

    spent_pool_by_currency: dict[str, UUID] = {}
    for ccy in pool_tagged_currencies:
        sys_pools = pool_repo.get_or_create_system_pools(currency=ccy)
        spent_pool_by_currency[ccy] = sys_pools[StoragePoolType.SPENT].id

    # ---- Build + post reversal of the source -----------------------
    source_postings = tx_repo.list_postings(tx_id)
    source_domain = DomainTransaction(
        id=source_storage.id,
        household_id=source_storage.household_id,
        date=source_storage.date,
        description=source_storage.description,
        reference=source_storage.reference,
        postings=tuple(
            DomainPosting(
                id=p.id,
                account_id=p.account_id,
                amount=Money(p.amount, p.currency),
                memo=p.memo,
                pool_id=p.pool_id,
            )
            for p in source_postings
        ),
        status=DomainTxStatus(source_storage.status.value),
        created_by_user_id=source_storage.created_by_user_id,
    )

    reversal_date = body.reversal_date or date_type.today()
    reversal_pending = build_reversal(
        source_domain,
        reversal_id=uuid4(),
        reversal_date=reversal_date,
        description=f"Reversal of {source_domain.description}: {body.reason}",
        actor_user_id=claims.user_id,
    )

    period_repo = PeriodRepository(session, claims.household_id)
    candidate_periods = _domain_periods(period_repo)
    try:
        reversal_posted = post_transaction(reversal_pending, periods=candidate_periods)
    except ClosedPeriodError as exc:
        raise PeriodClosedError(reason=str(exc)) from exc
    except UnbalancedTransactionError as exc:
        raise TransactionUnbalancedError(
            reason=f"Reversal failed to balance (Tulip bug): {exc}"
        ) from exc

    # ---- Build + post replacement ----------------------------------
    replacement_postings: tuple[DomainPosting, ...] = tuple(
        DomainPosting(
            id=uuid4(),
            account_id=p.account_id,
            amount=Money(p.amount, p.currency),
            memo=p.memo,
            pool_id=p.pool_id,
        )
        for p in body.postings
    )
    try:
        replacement_domain = DomainTransaction(
            id=uuid4(),
            household_id=claims.household_id,
            date=body.date,
            description=body.description,
            reference=body.reference,
            postings=replacement_postings,
            status=DomainTxStatus.PENDING,
            created_by_user_id=claims.user_id,
        )
    except ValueError as exc:
        raise TransactionInvalidError(reason=str(exc)) from exc
    try:
        replacement_posted = post_transaction(replacement_domain, periods=candidate_periods)
    except UnbalancedTransactionError as exc:
        raise TransactionUnbalancedError(reason=f"Replacement does not balance: {exc}") from exc
    except ClosedPeriodError as exc:
        raise PeriodClosedError(reason=str(exc)) from exc

    # ---- Derive paired shadow for the replacement ------------------
    account_types_by_id = {
        aid: DomainAccountType(a.type.value) for aid, a in accounts_by_id.items()
    }
    try:
        replacement_shadow = derive_paired_shadow_tx(
            replacement_posted,
            account_types_by_id=account_types_by_id,
            spent_pool_by_currency=spent_pool_by_currency,
        )
    except InvalidAccountTypePairingError as exc:
        raise PoolInvalidAccountTypePairingError(account_type="non-expense") from exc
    except MultiCurrencyPoolTaggingError as exc:
        raise TransactionInvalidError(reason=str(exc)) from exc
    except UnsupportedRefundShapedShadowTxError as exc:
        raise TransactionInvalidError(reason=str(exc)) from exc
    except ValueError as exc:
        raise ShadowLedgerInternalError() from exc

    # ---- Persist everything in one commit --------------------------
    voided_at = datetime.now(tz=UTC)
    tx_repo.persist_reversal(tx_id, reversal_posted, voided_at=voided_at)

    saved_replacement = tx_repo.save_balanced(replacement_posted, notes=body.notes)

    if replacement_shadow is not None:
        # The replacement gets its own paired shadow when any posting
        # carries ``pool_id`` (ADR-0001). The id is not surfaced in the
        # response since the caller queries the replacement via
        # ``GET /v1/transactions/{id}`` to discover it.
        ShadowTransactionRepository(session, claims.household_id).save_balanced(replacement_shadow)

    # Auto-void the source's paired shadow if it had one (same atomic
    # commit). Mirrors void_transaction.
    source_shadow_repo = ShadowTransactionRepository(session, claims.household_id)
    source_paired_shadow_id = source_shadow_repo.get_paired_id_for_main_tx(tx_id)
    if source_paired_shadow_id is not None:
        source_shadow_repo.void(source_paired_shadow_id, voided_at=voided_at)

    audit_after: dict[str, str] = {
        "reversal_id": str(reversal_posted.id),
        "replacement_id": str(saved_replacement.id),
        "reason": body.reason,
        "reversal_date": reversal_date.isoformat(),
    }
    if source_paired_shadow_id is not None:
        audit_after["paired_shadow_tx_id_voided"] = str(source_paired_shadow_id)
    AuditLogWriter(session, claims.household_id).write(
        action="replace",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=tx_id,
        before={"voided_by_transaction_id": None},
        after=audit_after,
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "transaction.replaced",
        source_id=str(tx_id),
        reversal_id=str(reversal_posted.id),
        replacement_id=str(saved_replacement.id),
        paired_shadow_tx_id_voided=(
            str(source_paired_shadow_id) if source_paired_shadow_id else None
        ),
    )
    return TransactionReplaceResponse(
        source_id=tx_id,
        reversal_id=reversal_posted.id,
        replacement_id=saved_replacement.id,
        voided_at=voided_at,
        paired_shadow_tx_id_voided=source_paired_shadow_id,
    )


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


def _resolve_notes_patch(body: TransactionUpdate) -> str | None | object:
    """Return UNSET if ``notes`` was omitted; else the body value (str or None).

    Distinguishes "don't touch the column" from "explicitly clear it".
    """
    if "notes" in body.model_fields_set:
        return body.notes
    return UNSET


@router.patch(
    "/{tx_id}",
    response_model=TransactionRead,
    responses={
        400: problem_response("account.unknown", "account.placeholder_posting", "tag.invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("transaction.not_found"),
        409: problem_response("transaction.not_editable"),
        422: problem_response("validation.failed"),
    },
)
def patch_transaction(
    tx_id: UUID,
    body: TransactionUpdate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionRead:
    """Edit a PENDING transaction. POSTED / RECONCILED return 409."""
    settings = get_settings()
    tx_repo = TransactionRepository(session, claims.household_id, master_key=settings.master_key)
    existing = tx_repo.get(tx_id)
    if existing is None:
        raise TransactionNotFoundError()

    # Merge body into existing values; PATCH semantics — fields omitted from
    # the body keep their current value.
    new_date = body.date if body.date is not None else existing.date
    new_desc = body.description if body.description is not None else existing.description
    new_ref = body.reference if body.reference is not None else existing.reference

    if body.postings is not None:
        # Validate account refs before delegating to the repo.
        accounts_repo = AccountRepository(session, claims.household_id)
        for p in body.postings:
            a = accounts_repo.get(p.account_id)
            if a is None:
                raise AccountUnknownError(account_id=str(p.account_id))
            if a.is_placeholder:
                from tulip_api.errors import AccountPlaceholderPostingError

                raise AccountPlaceholderPostingError(account_id=str(p.account_id))
        new_postings: tuple[DomainPosting, ...] = tuple(
            DomainPosting(
                id=uuid4(),
                account_id=p.account_id,
                amount=Money(p.amount, p.currency),
                memo=p.memo,
                pool_id=p.pool_id,
            )
            for p in body.postings
        )
    else:
        existing_postings = tx_repo.list_postings(tx_id)
        new_postings = tuple(
            DomainPosting(
                id=p.id,
                account_id=p.account_id,
                amount=Money(p.amount, p.currency),
                memo=p.memo,
                pool_id=p.pool_id,
            )
            for p in existing_postings
        )

    before_snapshot: dict[str, object] = {
        "date": existing.date.isoformat(),
        "description": existing.description,
        "reference": existing.reference,
    }
    notes_patch = _resolve_notes_patch(body)
    if notes_patch is not UNSET:
        before_snapshot["notes_present"] = existing.notes_encrypted is not None
    try:
        tx_repo.update_pending(
            tx_id,
            date=new_date,
            description=new_desc,
            reference=new_ref,
            postings=new_postings,
            notes=notes_patch,  # type: ignore[arg-type]
        )
    except RepoNotEditableError as exc:
        raise TransactionNotEditableError() from exc

    # #39: PATCH semantics for tags — omitting the field leaves the
    # current set alone; passing a list (including []) replaces it.
    if body.tags is not None:
        try:
            TransactionTagRepository(session, claims.household_id).set_tags(tx_id, list(body.tags))
        except RepoTagInvalidError as exc:
            raise TagInvalidError(reason=str(exc)) from exc

    after_snapshot: dict[str, object] = {
        "date": new_date.isoformat(),
        "description": new_desc,
        "reference": new_ref,
    }
    if notes_patch is not UNSET:
        after_snapshot["notes_present"] = notes_patch is not None
    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=tx_id,
        before=before_snapshot,
        after=after_snapshot,
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("transaction.updated", tx_id=str(tx_id))
    return _read_response(tx_id, claims.household_id, session)


@router.patch(
    "/{tx_id}/description",
    response_model=TransactionRead,
    responses={
        **FRAMEWORK_BODY_RESPONSES,
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("transaction.not_found"),
        409: problem_response("transaction.not_rectifiable"),
    },
)
def rectify_transaction_description(
    tx_id: UUID,
    body: TransactionRectifyRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TransactionRead:
    """Rectify a POSTED / RECONCILED transaction's header fields (GDPR Art. 16, #242).

    Mutates ``description`` / ``reference`` / ``notes_encrypted`` in place
    on the original row; postings, status, and date are unchanged. When the
    transaction has been voided, the reversal sibling's description (which
    the void route built as ``f"Reversal of {old}: {reason}"``) is
    rewritten in place so the source's pre-rectification description does
    not survive at rest in the reversal row.

    The OLD values are written verbatim into the audit row's
    ``before_snapshot``. Per the Art. 17(3)(e) integrity carve-out, the
    audit row preserves them until the user is later erased (at which
    point :func:`tulip_api.routers.users.delete_user` nulls the
    ``before_snapshot`` / ``after_snapshot`` blobs for rows referencing
    that user).
    """
    settings = get_settings()
    tx_repo = TransactionRepository(session, claims.household_id, master_key=settings.master_key)
    existing = tx_repo.get(tx_id)
    if existing is None:
        raise TransactionNotFoundError()

    fields_set = body.model_fields_set

    before_snapshot: dict[str, object] = {}
    if "description" in fields_set:
        before_snapshot["description"] = existing.description
    if "reference" in fields_set:
        before_snapshot["reference"] = existing.reference
    if "notes" in fields_set:
        before_snapshot["notes_present"] = existing.notes_encrypted is not None

    # The schema validator forbids body.description being None when the
    # key is set; assert for type-narrowing.
    description_arg: str | object
    if "description" in fields_set:
        assert body.description is not None  # noqa: S101 — guaranteed by schema validator
        description_arg = body.description
    else:
        description_arg = UNSET
    try:
        _, reversal_id_rewritten = tx_repo.rectify_posted(
            tx_id,
            description=description_arg,
            reference=body.reference if "reference" in fields_set else UNSET,
            notes=body.notes if "notes" in fields_set else UNSET,
        )
    except RepoNotRectifiableError as exc:
        raise TransactionNotRectifiableError() from exc

    after_snapshot: dict[str, object] = {}
    if "description" in fields_set:
        after_snapshot["description"] = body.description
    if "reference" in fields_set:
        after_snapshot["reference"] = body.reference
    if "notes" in fields_set:
        after_snapshot["notes_present"] = body.notes is not None

    metadata: dict[str, object] | None = None
    if reversal_id_rewritten is not None:
        metadata = {"reversal_id_rewritten": str(reversal_id_rewritten)}

    AuditLogWriter(session, claims.household_id).write(
        action="description_rectified",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=tx_id,
        before=before_snapshot,
        after=after_snapshot,
        metadata=metadata,
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "transaction.description_rectified",
        tx_id=str(tx_id),
        reversal_id_rewritten=str(reversal_id_rewritten) if reversal_id_rewritten else None,
    )
    return _read_response(tx_id, claims.household_id, session)


@router.delete(
    "/{tx_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("transaction.not_found"),
        409: problem_response("transaction.not_deletable"),
    },
)
def delete_transaction(
    tx_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Hard-delete a PENDING transaction. POSTED / RECONCILED return 409."""
    tx_repo = TransactionRepository(session, claims.household_id)
    existing = tx_repo.get(tx_id)
    if existing is None:
        raise TransactionNotFoundError()

    before_snapshot = {
        "date": existing.date.isoformat(),
        "description": existing.description,
        "reference": existing.reference,
        "status": existing.status.value,
    }
    try:
        tx_repo.delete_pending(tx_id)
    except RepoNotDeletableError as exc:
        raise TransactionNotDeletableError() from exc

    AuditLogWriter(session, claims.household_id).write(
        action="delete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="transaction",
        entity_id=tx_id,
        before=before_snapshot,
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("transaction.deleted", tx_id=str(tx_id))


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
    id_prefix: str | None = Query(
        default=None,
        description=(
            "Restrict to transactions whose UUID begins with this hex prefix "
            "(case-insensitive). Hyphens are accepted so callers can paste a "
            "partial UUID like '5df7-822c'. Excludes LIKE wildcards by regex."
        ),
        pattern="^[0-9a-fA-F-]{1,36}$",
    ),
    tag: str | None = Query(
        default=None,
        description=(
            "Restrict to transactions that carry this tag (#39 v1). "
            "Tags are case-insensitive on lookup. Single-tag filter only "
            "in v1; multi-tag / boolean grammar is a follow-up slice."
        ),
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
    on both ends). ``status`` is one of the lifecycle states. ``tag``
    restricts to transactions carrying the given label (#39 v1).
    """
    storage_status: StorageTxStatus | None = (
        StorageTxStatus(status_) if status_ is not None else None
    )
    tag_tx_ids: list[UUID] | None
    if tag is not None:
        try:
            tag_tx_ids = TransactionTagRepository(
                session, claims.household_id
            ).find_transaction_ids_by_tag(tag)
        except RepoTagInvalidError as exc:
            raise TagInvalidError(reason=str(exc)) from exc
        if not tag_tx_ids:
            return []
    else:
        tag_tx_ids = None
    rows = TransactionRepository(session, claims.household_id).list_headers(
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        status=storage_status,
        id_prefix=id_prefix,
        limit=limit,
    )
    if tag_tx_ids is not None:
        tag_id_set = set(tag_tx_ids)
        rows = [r for r in rows if r.id in tag_id_set]
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


def _read_response(
    tx_id: UUID,
    household_id: UUID,
    session: Session,
    *,
    paired_shadow_tx_id: UUID | None = None,
) -> TransactionRead:
    settings = get_settings()
    repo = TransactionRepository(session, household_id, master_key=settings.master_key)
    header = repo.get(tx_id)
    assert header is not None  # caller verifies before invoking  # noqa: S101
    postings = repo.list_postings(tx_id)
    if paired_shadow_tx_id is None:
        # GET / list paths don't have it pre-resolved; look it up.
        paired_shadow_tx_id = ShadowTransactionRepository(
            session, household_id
        ).get_paired_id_for_main_tx(tx_id)
    tags = TransactionTagRepository(session, household_id).list_tags(tx_id)
    return TransactionRead(
        id=header.id,
        date=header.date,
        description=header.description,
        reference=header.reference,
        notes=repo.decrypt_notes(header),
        status=header.status.value,
        postings=[
            PostingRead(
                id=p.id,
                account_id=p.account_id,
                amount=p.amount,
                currency=p.currency,
                memo=p.memo,
                pool_id=p.pool_id,
            )
            for p in postings
        ],
        paired_shadow_tx_id=paired_shadow_tx_id,
        voided_by_transaction_id=header.voided_by_transaction_id,
        voided_at=header.voided_at,
        tags=tags,
    )


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None
