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
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.auth.passwords import verify_password
from tulip_api.deps import get_session
from tulip_api.errors import (
    DuplicateEmailError,
    InvalidCredentialsError,
    LastAdminDeletionError,
    ReauthRequiredError,
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
    UserAIPolicyPatchRequest,
    UserAIPolicyRead,
    UserDataExport,
    UserMeRead,
    UserProfilePatchRequest,
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

    # Redact historic audit-log PII for rows referencing this user (either
    # as actor or as the entity being acted on). The BEFORE UPDATE trigger
    # (#333 / M-22) blocks audit_log UPDATEs by default; the
    # ``audit_log_pii_redaction_allowed`` context manager carves out this
    # one legitimate site. The tombstone row below is written *after* the
    # context exits so it isn't caught by the scrub.
    from tulip_storage.audit_log_helpers import audit_log_pii_redaction_allowed

    with audit_log_pii_redaction_allowed(session):
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


@router.patch(
    "/me",
    response_model=UserMeRead,
    responses={
        401: problem_response(
            "auth.unauthorized", "auth.invalid_credentials", "auth.reauth_required"
        ),
        409: problem_response("auth.duplicate_email"),
        422: problem_response("validation.failed"),
    },
)
def patch_own_profile(
    body: UserProfilePatchRequest,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> UserMeRead:
    """Rectify the caller's own display name and/or email (GDPR Art. 16, #242).

    Changing ``email`` is gated on re-auth: the caller must include
    ``current_password`` in the same request body. ``display_name`` may
    be updated without re-auth. The audit row records cleartext
    before/after snapshots of the fields actually changed — these get
    nulled when the user is later erased (right-to-erasure cascades to
    audit-PII per :func:`delete_user`).
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UserNotFoundError()

    fields_set = body.model_fields_set

    before_snapshot: dict[str, object] = {}
    after_snapshot: dict[str, object] = {}

    if "email" in fields_set:
        if "current_password" not in fields_set or body.current_password is None:
            raise ReauthRequiredError()
        if not verify_password(body.current_password, user.password_hash):
            raise InvalidCredentialsError()
        before_snapshot["email"] = user.email
        after_snapshot["email"] = body.email
        user.email = body.email  # type: ignore[assignment]

    if "display_name" in fields_set:
        before_snapshot["display_name"] = user.display_name
        after_snapshot["display_name"] = body.display_name
        user.display_name = body.display_name  # type: ignore[assignment]

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateEmailError() from exc

    AuditLogWriter(session, claims.household_id).write(
        action="profile_updated",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="user",
        entity_id=user.id,
        before=before_snapshot,
        after=after_snapshot,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    return UserMeRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
    )


def _apply_ai_policy(
    *,
    session: Session,
    request: Request,
    actor_claims: Claims,
    target_user: User,
    body: UserAIPolicyPatchRequest,
) -> UserAIPolicyRead:
    """Replace the target user's ``ai_policy`` and emit an audit row (#239).

    Empty body clears the override (NULL = inherit household). Otherwise
    we store the validated JSON shape verbatim. Returns the new policy
    shape; the audit row carries before/after snapshots.
    """
    before = target_user.ai_policy
    new_policy: dict[str, Any] | None
    if body.capabilities is None:
        new_policy = None
    else:
        # Dump via Pydantic so Literals coerce to plain strings and any
        # explicit ``None`` capability keys drop out cleanly.
        dumped = body.model_dump(exclude_none=True)
        new_policy = dumped if dumped else None
    target_user.ai_policy = new_policy

    AuditLogWriter(session, actor_claims.household_id).write(
        action="user.ai_policy_set",
        actor_kind="user",
        actor_user_id=actor_claims.user_id,
        entity_type="user",
        entity_id=target_user.id,
        before=before,
        after=new_policy,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    return UserAIPolicyRead(user_id=target_user.id, ai_policy=new_policy)


@router.put(
    "/me/ai-policy",
    response_model=UserAIPolicyRead,
    responses={
        401: problem_response("auth.unauthorized"),
        422: problem_response("validation.failed"),
    },
)
def put_own_ai_policy(
    body: UserAIPolicyPatchRequest,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> UserAIPolicyRead:
    """Set the caller's own per-user AI policy override (#239).

    Members can ratchet the household's policy *up* (stricter). The merge
    happens at read time in ``tulip_ai.policy.resolve_policy``; this
    endpoint just stores the override JSON.
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UserNotFoundError()
    return _apply_ai_policy(
        session=session,
        request=request,
        actor_claims=claims,
        target_user=user,
        body=body,
    )


@router.put(
    "/{user_id}/ai-policy",
    response_model=UserAIPolicyRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("user.not_found"),
        422: problem_response("validation.failed"),
    },
)
def put_user_ai_policy(
    user_id: UUID,
    body: UserAIPolicyPatchRequest,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> UserAIPolicyRead:
    """Admin: set the AI policy override for any user in the caller's household (#239)."""
    user = session.get(User, (claims.household_id, user_id))
    if user is None:
        raise UserNotFoundError()
    return _apply_ai_policy(
        session=session,
        request=request,
        actor_claims=claims,
        target_user=user,
        body=body,
    )


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
            ai_policy=user.ai_policy,
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
