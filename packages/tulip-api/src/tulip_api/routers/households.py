"""Household administration — right-to-erasure flow (H-2/H-3, #235).

Two endpoints implement GDPR Art. 17 / CCPA §1798.105 for an entire
household:

* ``POST /v1/households/me/erase-request`` — admin issues a fresh
  confirmation token (returned exactly once). The plaintext is required
  on the matching ``DELETE`` call; the server stores only its SHA-256.
* ``DELETE /v1/households/me`` — admin submits the token in
  ``X-Erasure-Token``; on match, the household row is deleted, schema
  ``ondelete="CASCADE"`` clears every child table, and the household's
  attachment ciphertext files are unlinked from disk (after first
  checking that no other household still references the same content
  hash — within-household dedup means a given blob can in principle
  back rows in other tenants too).

The token TTL is short (15 minutes) so a leaked request from an old
browser tab can't be replayed.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from tulip_api.auth.deps import require_role
from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.errors import (
    HouseholdErasureNotRequestedError,
    HouseholdErasureTokenInvalidError,
    problem_response,
)
from tulip_storage.models import Attachment, Household, PendingHouseholdErasure
from tulip_storage.repositories import AuditLogWriter

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/households", tags=["households"])

#: How long the erasure confirmation token stays valid.
_ERASURE_TOKEN_TTL: timedelta = timedelta(minutes=15)


class EraseRequestResponse(BaseModel):
    """Body returned from ``POST /v1/households/me/erase-request``."""

    token: str
    expires_at: datetime


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


@router.post(
    "/me/erase-request",
    response_model=EraseRequestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def request_erasure(
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> EraseRequestResponse:
    """Step 1 — issue a confirmation token for household erasure.

    Overwrites any prior outstanding request (one slot per household);
    only the most recent token is valid. Plaintext is returned exactly
    once and stored hashed.
    """
    token_plain = secrets.token_urlsafe(32)
    now = datetime.now(tz=UTC)
    expires = now + _ERASURE_TOKEN_TTL

    existing = session.get(PendingHouseholdErasure, claims.household_id)
    if existing is None:
        session.add(
            PendingHouseholdErasure(
                household_id=claims.household_id,
                token_hash=_hash_token(token_plain),
                requested_at=now,
                expires_at=expires,
                requested_by_user_id=claims.user_id,
            )
        )
    else:
        existing.token_hash = _hash_token(token_plain)
        existing.requested_at = now
        existing.expires_at = expires
        existing.requested_by_user_id = claims.user_id

    AuditLogWriter(session, claims.household_id).write(
        action="household.erase_requested",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="household",
        entity_id=claims.household_id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    return EraseRequestResponse(token=token_plain, expires_at=expires)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized", "household.erasure_token_invalid"),
        403: problem_response("auth.forbidden"),
        409: problem_response("household.erasure_not_requested"),
    },
)
def erase_household(
    request: Request,
    x_erasure_token: str | None = Header(default=None, alias="X-Erasure-Token"),
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Step 2 — erase the entire household, attachments and all.

    Validates the confirmation token from step 1, captures the set of
    attachment content hashes referenced by this household (so we can
    unlink the ciphertext after the schema cascade clears the rows),
    deletes the ``households`` row (CASCADE handles every child table),
    then unlinks orphaned attachment blobs from disk.
    """
    pending = session.get(PendingHouseholdErasure, claims.household_id)
    if pending is None:
        raise HouseholdErasureNotRequestedError()

    if x_erasure_token is None or _hash_token(x_erasure_token) != pending.token_hash:
        raise HouseholdErasureTokenInvalidError()

    now = datetime.now(tz=UTC)
    expires_at = pending.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise HouseholdErasureTokenInvalidError()

    # Snapshot the content hashes before the cascade clears the rows.
    content_hashes = {
        row[0]
        for row in session.execute(
            select(Attachment.content_hash).where(Attachment.household_id == claims.household_id)
        ).all()
    }

    # Tombstone (no PII; just the structural fact that this household existed).
    AuditLogWriter(session, claims.household_id).write(
        action="household.deleted",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="household",
        entity_id=claims.household_id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.flush()

    # Drop the household row → CASCADE removes audit_log, users, sessions,
    # mfa_recovery_codes, accounts, transactions, postings, periods,
    # allocation_pools, envelopes, sinking_funds, shadow_transactions,
    # shadow_postings, csv_profiles, import_batches, reconciliations,
    # attachments, ai_invocations, notifications, pending_proposals,
    # scheduled_jobs, pending_household_erasures.
    #
    # The audit_log BEFORE DELETE trigger (#333 / M-22) blocks every
    # row-delete; it has to come down for the FK cascade to clear the
    # household's audit_log rows. The context manager drops + recreates
    # the trigger; the try/finally on its exit guarantees the trigger
    # is reinstated even if the DELETE fails.
    from tulip_storage.audit_log_helpers import audit_log_deletion_allowed

    with audit_log_deletion_allowed(session):
        session.execute(sa_delete(Household).where(Household.id == claims.household_id))
        session.commit()

    # Unlink orphaned attachment ciphertext. A blob is orphaned iff no
    # other household still references its content_hash (within-household
    # dedup means cross-household sharing is theoretically possible).
    for h in content_hashes:
        still_used = session.execute(
            select(Attachment.id).where(Attachment.content_hash == h).limit(1)
        ).first()
        if still_used is None:
            blob = settings.attachment_root / h
            try:
                blob.unlink()
            except FileNotFoundError:
                continue


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
