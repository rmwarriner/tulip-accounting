"""GET/POST/PATCH/DELETE /v1/accounts."""

from __future__ import annotations

import hashlib
from datetime import date as date_type
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    AccountNotFoundError,
    AccountNotRedactableError,
    AccountParentCurrencyMismatchError,
    AccountParentCycleError,
    AccountParentNotFoundError,
    AccountParentTypeMismatchError,
    AccountParentVisibilityViolationError,
    ForbiddenError,
    problem_response,
)
from tulip_api.schemas.account import AccountCreate, AccountRead, AccountUpdate
from tulip_api.schemas.balance import AccountBalanceRead
from tulip_api.schemas.lifecycle import DeactivationResponse, RedactionResponse
from tulip_core.money import Money
from tulip_storage.models import AccountType
from tulip_storage.repositories import AccountRepository, AuditLogWriter, TransactionRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims
    from tulip_storage.models import Account


router = APIRouter(prefix="/v1/accounts", tags=["accounts"])
log = structlog.get_logger("tulip_api.accounts")


def _to_read(a: Account) -> AccountRead:
    return AccountRead(
        id=a.id,
        code=a.code,
        name=a.name,
        type=a.type.value,
        subtype=a.subtype,
        currency=a.currency,
        visibility=a.visibility,
        is_active=a.is_active,
        parent_account_id=a.parent_account_id,
    )


def _filter_for_role(account: Account, claims: Claims) -> bool:
    """Return True iff the caller may see this account."""
    if account.visibility == "shared":
        return True
    # private — admin sees all; member/viewer must be the creator.
    if claims.role == "admin":
        return True
    return account.created_by_user_id == claims.user_id


def _validate_parent(
    repo: AccountRepository,
    *,
    parent_id: UUID,
    child_id: UUID | None,
    child_type: str,
    child_currency: str,
    child_visibility: str,
    claims: Claims,
) -> Account:
    """Validate a proposed parent for an account being created or updated.

    Returns the parent ``Account`` row on success; raises one of the
    ``account.parent_*`` Problem Details errors on any failure. Rules
    enforced (per #42):

    * Parent exists in this household, is visible to the caller, and is
      active. Otherwise → ``account.parent_not_found``.
    * Parent's ``type`` must equal the child's. → ``account.parent_type_mismatch``.
    * Parent's ``currency`` must equal the child's (#44 tracks the
      multi-currency relaxation). → ``account.parent_currency_mismatch``.
    * Shared child must not live under a private parent. →
      ``account.parent_visibility_violation``.
    * For PATCH (``child_id`` is not None): the proposed parent must
      not be a descendant of the child. We walk up from ``parent_id``
      via ``parent_account_id`` and reject if we hit ``child_id``. →
      ``account.parent_cycle``.

    The cycle walk also catches the trivial self-parent case (a PATCH
    that sets ``parent_account_id = id``).
    """
    parent = repo.get(parent_id)
    if parent is None or not parent.is_active or not _filter_for_role(parent, claims):
        raise AccountParentNotFoundError()

    if child_id is not None:
        # Walk up the proposed-parent's ancestor chain. If we ever
        # encounter ``child_id``, applying the change creates a cycle.
        # Also catches self-parent (parent_id == child_id) on the first hop.
        cursor: Account | None = parent
        while cursor is not None:
            if cursor.id == child_id:
                raise AccountParentCycleError()
            if cursor.parent_account_id is None:
                break
            cursor = repo.get(cursor.parent_account_id)

    if parent.type.value != child_type:
        raise AccountParentTypeMismatchError(child_type=child_type, parent_type=parent.type.value)
    if parent.currency != child_currency:
        raise AccountParentCurrencyMismatchError(
            child_currency=child_currency, parent_currency=parent.currency
        )
    if parent.visibility == "private" and child_visibility != "private":
        raise AccountParentVisibilityViolationError()

    return parent


@router.get(
    "",
    response_model=list[AccountRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_accounts(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[AccountRead]:
    """List active accounts visible to the caller."""
    repo = AccountRepository(session, claims.household_id)
    rows = [a for a in repo.list_active() if _filter_for_role(a, claims)]
    return [_to_read(a) for a in rows]


@router.post(
    "",
    response_model=AccountRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("account.parent_not_found"),
        400: problem_response(
            "request.body_invalid",
            "account.parent_type_mismatch",
            "account.parent_currency_mismatch",
            "account.parent_visibility_violation",
        ),
        422: problem_response("validation.failed"),
    },
)
def create_account(
    body: AccountCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AccountRead:
    """Create a new account in the caller's household."""
    repo = AccountRepository(session, claims.household_id)
    if body.parent_account_id is not None:
        _validate_parent(
            repo,
            parent_id=body.parent_account_id,
            child_id=None,
            child_type=body.type,
            child_currency=body.currency,
            child_visibility=body.visibility,
            claims=claims,
        )
    a = repo.create(
        name=body.name,
        type=AccountType(body.type),
        currency=body.currency,
        code=body.code,
        subtype=body.subtype,
        parent_account_id=body.parent_account_id,
        visibility=body.visibility,
        created_by_user_id=claims.user_id,
    )
    AuditLogWriter(session, claims.household_id).write(
        action="create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="account",
        entity_id=a.id,
        after={"name": a.name, "type": a.type.value, "currency": a.currency},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("account.created", account_id=str(a.id))
    return _to_read(a)


@router.get(
    "/{account_id}",
    response_model=AccountRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("account.not_found"),
    },
)
def get_account(
    account_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AccountRead:
    """Fetch an account by id (404 if not in this household or not visible)."""
    a = AccountRepository(session, claims.household_id).get(account_id)
    if a is None or not _filter_for_role(a, claims):
        raise AccountNotFoundError()
    return _to_read(a)


@router.patch(
    "/{account_id}",
    response_model=AccountRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("account.not_found", "account.parent_not_found"),
        400: problem_response(
            "request.body_invalid",
            "account.parent_type_mismatch",
            "account.parent_currency_mismatch",
            "account.parent_visibility_violation",
            "account.parent_cycle",
        ),
        422: problem_response("validation.failed"),
    },
)
def update_account(
    account_id: UUID,
    body: AccountUpdate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AccountRead:
    """Update mutable fields. Member cannot edit private accounts they didn't create."""
    repo = AccountRepository(session, claims.household_id)
    a = repo.get(account_id)
    if a is None or not _filter_for_role(a, claims):
        raise AccountNotFoundError()
    if (
        claims.role == "member"
        and a.visibility == "private"
        and a.created_by_user_id != claims.user_id
    ):
        raise ForbiddenError(
            "Members can only edit private accounts they created themselves. "
            "Ask an admin, or have the original creator make the change."
        )

    # Visibility may be changing in this PATCH; if so, the new visibility
    # is what we validate the parent against. Same for parent itself.
    new_visibility = body.visibility if body.visibility is not None else a.visibility
    if body.parent_account_id is not None:
        _validate_parent(
            repo,
            parent_id=body.parent_account_id,
            child_id=a.id,
            child_type=a.type.value,
            child_currency=a.currency,
            child_visibility=new_visibility,
            claims=claims,
        )

    before = {
        "name": a.name,
        "code": a.code,
        "visibility": a.visibility,
        "parent_account_id": str(a.parent_account_id) if a.parent_account_id else None,
    }
    if body.name is not None:
        a.name = body.name
    if body.code is not None:
        a.code = body.code
    if body.subtype is not None:
        a.subtype = body.subtype
    if body.visibility is not None:
        a.visibility = body.visibility
    if body.parent_account_id is not None:
        a.parent_account_id = body.parent_account_id
    session.flush()

    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="account",
        entity_id=a.id,
        before=before,
        after={
            "name": a.name,
            "code": a.code,
            "visibility": a.visibility,
            "parent_account_id": str(a.parent_account_id) if a.parent_account_id else None,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    return _to_read(a)


@router.get(
    "/{account_id}/balance",
    response_model=AccountBalanceRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("account.not_found"),
    },
)
def get_account_balance(
    account_id: UUID,
    as_of: date_type | None = Query(  # noqa: B008 — FastAPI uses Query() in defaults
        default=None,
        description=(
            "Optional point-in-time date (YYYY-MM-DD). Includes only "
            "transactions on or before this date. Defaults to today."
        ),
    ),
    include_pending: bool = Query(
        default=False,
        description=(
            "When true, fold PENDING transactions into the balance — the "
            "'what if all pending is real' view. Default false keeps the "
            "posted-only ledger semantics. The response then carries "
            "pending_included=true and pending_count."
        ),
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AccountBalanceRead:
    """Return the ledger balance of an account in its primary currency.

    By default only POSTED + RECONCILED contribute. ``include_pending=true``
    (#274) widens the sum to PENDING transactions too. Postings in other
    currencies on this account (e.g. FX postings) are not included; use
    the trial-balance report for the multi-currency view.
    """
    a = AccountRepository(session, claims.household_id).get(account_id)
    if a is None or not _filter_for_role(a, claims):
        raise AccountNotFoundError()

    effective_as_of = as_of or date_type.today()
    tx_repo = TransactionRepository(session, claims.household_id)
    raw_balance = tx_repo.balance_for_account(
        a.id,
        currency=a.currency,
        as_of=effective_as_of,
        include_pending=include_pending,
    )
    pending_count = (
        tx_repo.count_pending_for_account(a.id, currency=a.currency, as_of=effective_as_of)
        if include_pending
        else 0
    )
    # Quantize to the currency's minor units so the JSON representation
    # is the natural "12.50" rather than the storage-precision "12.50000000".
    balance = Money(raw_balance, a.currency).quantize_to_currency().amount
    return AccountBalanceRead(
        account_id=a.id,
        code=a.code,
        name=a.name,
        currency=a.currency,
        balance=balance,
        as_of=effective_as_of,
        pending_included=include_pending,
        pending_count=pending_count,
    )


#: Field types an account retains after a soft-delete (deactivate). Erased
#: only by a follow-up POST /v1/accounts/{id}/redact (#236).
_ACCOUNT_PII_FIELDS = ["name", "external_account_number_encrypted", "notes_encrypted"]


@router.delete(
    "/{account_id}",
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("account.not_found"),
    },
)
def deactivate_account(
    account_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> DeactivationResponse:
    """Soft-delete (deactivate) an account. Admin only.

    DELETE *deactivates* — it does not erase. The account row, ``name``,
    and the encrypted PII columns all survive (posting FKs are
    ``ON DELETE RESTRICT``). The response says so honestly; use
    ``POST /v1/accounts/{id}/redact`` afterwards to erase the PII.
    """
    repo = AccountRepository(session, claims.household_id)
    try:
        a = repo.deactivate(account_id)
    except LookupError as exc:
        raise AccountNotFoundError() from exc
    AuditLogWriter(session, claims.household_id).write(
        action="delete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="account",
        entity_id=a.id,
        before={"is_active": True},
        after={"is_active": False},
        request_id=_request_uuid(request),
    )
    session.commit()
    return DeactivationResponse(data_retained=_ACCOUNT_PII_FIELDS)


@router.post(
    "/{account_id}/redact",
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("account.not_found"),
        409: problem_response("account.not_redactable"),
    },
)
def redact_account(
    account_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> RedactionResponse:
    """Erase a deactivated account's PII. Admin only.

    Nulls ``external_account_number_encrypted`` / ``notes_encrypted`` and
    replaces ``name`` with a non-PII placeholder. Postings keep their FK
    and amounts — ledger history is preserved. The account must already
    be deactivated (``409 account.not_redactable`` otherwise); there is
    no API path to re-activate it, so the erasure is final.
    """
    repo = AccountRepository(session, claims.household_id)
    a = repo.get(account_id)
    if a is None:
        raise AccountNotFoundError()
    if a.is_active:
        raise AccountNotRedactableError()
    placeholder = f"redacted-account-{hashlib.sha256(str(account_id).encode()).hexdigest()[:8]}"
    repo.redact(account_id, name=placeholder)
    AuditLogWriter(session, claims.household_id).write(
        action="redact",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="account",
        entity_id=account_id,
        before={"redacted": False},
        after={"redacted": True, "name": placeholder},
        request_id=_request_uuid(request),
    )
    session.commit()
    return RedactionResponse(fields_redacted=_ACCOUNT_PII_FIELDS)


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None
