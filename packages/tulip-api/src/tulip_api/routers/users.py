"""DELETE /v1/users/{user_id} — right-to-erasure for one user (H-2, #235).

GDPR Art. 17 + CCPA §1798.105 give an end-user the right to have their
account erased. The endpoint cascades sessions + MFA codes via the
existing schema ``ondelete="CASCADE"`` (no manual deletes needed), then
nulls out the deleted user's PII from historic ``audit_log`` JSON blobs.

The user's ``id`` is retained as a pseudonym across ``actor_user_id`` /
``entity_id`` columns — Art. 17(3)(e) permits this for audit-trail
integrity, and the schema doesn't carry FK from those columns back to
``users.id`` so nothing breaks when the row vanishes.

Admin-only; refuses when the target is the last admin in the household.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, or_, select, update

from tulip_api.auth.deps import require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    LastAdminDeletionError,
    UserNotFoundError,
    problem_response,
)
from tulip_storage.models import AuditLog, User, UserRole
from tulip_storage.repositories import AuditLogWriter

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/users", tags=["users"])


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("user.not_found"),
        409: problem_response("user.last_admin"),
    },
)
def delete_user(
    user_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Erase one user account from the caller's household.

    Cascade: schema-level ``ondelete="CASCADE"`` removes the user's
    ``sessions`` rows and ``mfa_recovery_codes`` rows. The user's
    ``id`` survives as a pseudonym in any historic ``audit_log`` row's
    ``actor_user_id`` / ``entity_id`` column; PII inside the JSON
    snapshot blobs of those rows is nulled.

    Refuses when the target is the last admin — without an admin no
    one can grant another user admin permissions, leaving the household
    self-locked.
    """
    user = session.get(User, (claims.household_id, user_id))
    if user is None:
        raise UserNotFoundError()

    if user.role == UserRole.ADMIN:
        admin_count = session.execute(
            select(func.count())
            .select_from(User)
            .where(User.household_id == claims.household_id, User.role == UserRole.ADMIN)
        ).scalar_one()
        if admin_count <= 1:
            raise LastAdminDeletionError()

    # Redact historic audit-log PII for rows referencing this user (either as
    # actor or as the entity being acted on). Tombstone row is written
    # afterward so it isn't caught by the same UPDATE.
    session.execute(
        update(AuditLog)
        .where(
            AuditLog.household_id == claims.household_id,
            or_(AuditLog.actor_user_id == user_id, AuditLog.entity_id == user_id),
        )
        .values(before_snapshot=None, after_snapshot=None, metadata_=None)
    )

    # Tombstone: structural only — role + caller-id, no email or display name.
    AuditLogWriter(session, claims.household_id).write(
        action="user.deleted",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="user",
        entity_id=user_id,
        metadata={"deleted_role": user.role.value},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    session.delete(user)
    session.commit()


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
