"""GET/POST/PATCH/DELETE /v1/accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import AccountNotFoundError, ForbiddenError, problem_response
from tulip_api.schemas.account import AccountCreate, AccountRead, AccountUpdate
from tulip_storage.models import AccountType
from tulip_storage.repositories import AccountRepository, AuditLogWriter

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
        400: problem_response("request.body_invalid"),
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
        404: problem_response("account.not_found"),
        400: problem_response("request.body_invalid"),
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

    before = {"name": a.name, "code": a.code, "visibility": a.visibility}
    if body.name is not None:
        a.name = body.name
    if body.code is not None:
        a.code = body.code
    if body.subtype is not None:
        a.subtype = body.subtype
    if body.visibility is not None:
        a.visibility = body.visibility
    session.flush()

    AuditLogWriter(session, claims.household_id).write(
        action="update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="account",
        entity_id=a.id,
        before=before,
        after={"name": a.name, "code": a.code, "visibility": a.visibility},
        request_id=_request_uuid(request),
    )
    session.commit()
    return _to_read(a)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
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
) -> None:
    """Soft-delete (deactivate) an account. Admin only."""
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


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None
