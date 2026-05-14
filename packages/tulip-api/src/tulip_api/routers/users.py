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

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, or_, select, update

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    LastAdminDeletionError,
    UserNotFoundError,
    problem_response,
)
from tulip_api.schemas.user import (
    AIInvocationExport,
    AttachmentMetadataExport,
    AuditLogExport,
    ProposalExport,
    RecoveryCodesStatusExport,
    SessionExport,
    TransactionExport,
    UserDataExport,
    UserRecordExport,
)
from tulip_storage.models import (
    AIInvocation,
    Attachment,
    AuditLog,
    MfaRecoveryCode,
    PendingProposal,
    Transaction,
    User,
    UserRole,
)
from tulip_storage.models import Session as DbSession
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


def _build_user_export(session: Session, user: User) -> UserDataExport:
    """Assemble the full data-subject-access envelope for ``user`` (#241).

    All queries are scoped to the user's household; the per-table filters
    select rows where this user is the actor / creator / uploader. The
    user's ``id`` is the stable pseudonym those columns carry.
    """
    hh = user.household_id
    uid = user.id

    sessions = [
        SessionExport(
            id=r.id,
            created_at=r.created_at,
            expires_at=r.expires_at,
            revoked_at=r.revoked_at,
            ip_address=r.ip_address,
            user_agent=r.user_agent,
        )
        for r in session.execute(
            select(DbSession).where(DbSession.household_id == hh, DbSession.user_id == uid)
        ).scalars()
    ]
    audit = [
        AuditLogExport(
            id=r.id,
            occurred_at=r.occurred_at,
            actor_kind=r.actor_kind,
            action=r.action,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            before_snapshot=r.before_snapshot,
            after_snapshot=r.after_snapshot,
            request_id=r.request_id,
            ip_address=r.ip_address,
            user_agent=r.user_agent,
            metadata=r.metadata_,
        )
        for r in session.execute(
            select(AuditLog).where(AuditLog.household_id == hh, AuditLog.actor_user_id == uid)
        ).scalars()
    ]
    invocations = [
        AIInvocationExport(
            id=r.id,
            created_at=r.created_at,
            capability=r.capability,
            provider=r.provider,
            model=r.model,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            cost_estimate_usd=r.cost_estimate_usd,
            outcome=r.outcome,
            prompt_json=r.prompt_json,
            response_text=r.response_text,
        )
        for r in session.execute(
            select(AIInvocation).where(
                AIInvocation.household_id == hh, AIInvocation.actor_user_id == uid
            )
        ).scalars()
    ]

    def _proposal(r: PendingProposal) -> ProposalExport:
        return ProposalExport(
            id=r.id,
            created_at=r.created_at,
            kind=r.kind,
            title=r.title,
            status=r.status,
            created_by_kind=r.created_by_kind,
            decided_at=r.decided_at,
            decision_note=r.decision_note,
        )

    proposals_created = [
        _proposal(r)
        for r in session.execute(
            select(PendingProposal).where(
                PendingProposal.household_id == hh,
                PendingProposal.created_by_user_id == uid,
            )
        ).scalars()
    ]
    proposals_decided = [
        _proposal(r)
        for r in session.execute(
            select(PendingProposal).where(
                PendingProposal.household_id == hh,
                PendingProposal.decided_by_user_id == uid,
            )
        ).scalars()
    ]
    attachments = [
        AttachmentMetadataExport(
            id=r.id,
            filename=r.filename,
            content_type=r.content_type,
            size_bytes=r.size_bytes,
            content_hash=r.content_hash,
            uploaded_at=r.uploaded_at,
        )
        for r in session.execute(
            select(Attachment).where(
                Attachment.household_id == hh, Attachment.uploaded_by_user_id == uid
            )
        ).scalars()
    ]
    recovery_rows = list(
        session.execute(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.household_id == hh, MfaRecoveryCode.user_id == uid
            )
        ).scalars()
    )
    recovery = RecoveryCodesStatusExport(
        total=len(recovery_rows),
        remaining=sum(1 for r in recovery_rows if r.used_at is None),
        used_at=sorted(r.used_at for r in recovery_rows if r.used_at is not None),
    )
    transactions = [
        TransactionExport(
            id=r.id,
            date=r.date,
            description=r.description,
            reference=r.reference,
            status=r.status.value,
            created_at=r.created_at,
        )
        for r in session.execute(
            select(Transaction).where(
                Transaction.household_id == hh, Transaction.created_by_user_id == uid
            )
        ).scalars()
    ]

    return UserDataExport(
        generated_at=datetime.now(tz=UTC),
        user=UserRecordExport(
            id=user.id,
            email=user.email,
            password_hash="***",  # noqa: S106 — masked placeholder, never the real hash
            display_name=user.display_name,
            role=user.role.value,
            totp_enrolled_at=user.totp_enrolled_at,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        sessions=sessions,
        audit_log_where_actor=audit,
        ai_invocations=invocations,
        proposals_created=proposals_created,
        proposals_decided=proposals_decided,
        attachments_uploaded=attachments,
        recovery_codes=recovery,
        transactions_created=transactions,
    )


@router.get(
    "/me/export",
    response_model=UserDataExport,
    responses={401: problem_response("auth.unauthorized")},
)
def export_own_data(
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> UserDataExport:
    """Export everything held about the calling user (GDPR Art. 15 / CCPA §1798.110).

    Any authenticated user may export their own data. The access itself
    is recorded as an ``audit_log`` row.
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UserNotFoundError()
    export = _build_user_export(session, user)
    AuditLogWriter(session, claims.household_id).write(
        action="user.data_exported",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="user",
        entity_id=claims.user_id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    return export


@router.get(
    "/{user_id}/export",
    response_model=UserDataExport,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("user.not_found"),
    },
)
def export_member_data(
    user_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> UserDataExport:
    """Admin: export everything held about a member of the caller's household.

    Scoped to the admin's household — a ``user_id`` from another
    household resolves to ``404``. The access is audited, attributing
    both the acting admin and the subject.
    """
    user = session.get(User, (claims.household_id, user_id))
    if user is None:
        raise UserNotFoundError()
    export = _build_user_export(session, user)
    AuditLogWriter(session, claims.household_id).write(
        action="user.data_exported",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="user",
        entity_id=user_id,
        metadata={"exported_by_admin": True},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    return export


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
