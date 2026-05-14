"""Schemas for the GDPR Art. 15 / CCPA data-subject-access export (#241).

``UserDataExport`` is the envelope returned by ``GET /v1/users/me/export``
and ``GET /v1/users/{user_id}/export`` — it enumerates everything the
system holds about one data subject. The nested models are deliberately
shaped to the *subject's* data: rows where they are the actor / creator /
uploader, plus their own user record (with ``password_hash`` masked).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class UserRecordExport(BaseModel):
    """The ``users`` row itself. ``password_hash`` is always masked."""

    id: UUID
    email: str
    password_hash: str
    display_name: str
    role: str
    totp_enrolled_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionExport(BaseModel):
    """One refresh-token session. The token hash itself is not exported."""

    id: UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    ip_address: str | None
    user_agent: str | None


class AuditLogExport(BaseModel):
    """An ``audit_log`` row where the subject is the actor."""

    id: UUID
    occurred_at: datetime
    actor_kind: str
    action: str
    entity_type: str
    entity_id: UUID
    before_snapshot: dict[str, Any] | None
    after_snapshot: dict[str, Any] | None
    request_id: UUID | None
    ip_address: str | None
    user_agent: str | None
    metadata: dict[str, Any] | None


class AIInvocationExport(BaseModel):
    """An ``ai_invocations`` row attributed to the subject."""

    id: UUID
    created_at: datetime
    capability: str
    provider: str | None
    model: str | None
    tokens_in: int
    tokens_out: int
    # The model annotates this float; it's a per-call USD estimate for
    # cost-cap accounting, not ledger money — no Money value object here.
    cost_estimate_usd: float
    outcome: str
    prompt_json: str | None
    response_text: str | None


class ProposalExport(BaseModel):
    """A ``pending_proposals`` row the subject created or decided."""

    id: UUID
    created_at: datetime
    kind: str
    title: str
    status: str
    created_by_kind: str
    decided_at: datetime | None
    decision_note: str | None


class AttachmentMetadataExport(BaseModel):
    """Metadata for an attachment the subject uploaded — never the bytes."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    uploaded_at: datetime


class RecoveryCodesStatusExport(BaseModel):
    """MFA recovery-code status — counts + use timestamps, never the hashes."""

    total: int
    remaining: int
    used_at: list[datetime]


class TransactionExport(BaseModel):
    """A ``transactions`` row the subject created. ``notes_encrypted`` excluded."""

    id: UUID
    date: date
    description: str
    reference: str | None
    status: str
    created_at: datetime


class UserDataExport(BaseModel):
    """Everything held about one data subject (GDPR Art. 15 / CCPA §1798.110)."""

    generated_at: datetime
    user: UserRecordExport
    sessions: list[SessionExport]
    audit_log_where_actor: list[AuditLogExport]
    ai_invocations: list[AIInvocationExport]
    proposals_created: list[ProposalExport]
    proposals_decided: list[ProposalExport]
    attachments_uploaded: list[AttachmentMetadataExport]
    recovery_codes: RecoveryCodesStatusExport
    transactions_created: list[TransactionExport]
