"""POST /v1/auth/{register,login,refresh,logout}.

Audit log entries are written for every successful auth event. Failed
logins are intentionally NOT written here (would create a vector for
filling the audit log with garbage); they're emitted to the app log via
structlog instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tulip_api.auth.passwords import hash_password, verify_password
from tulip_api.auth.tokens import (
    DEFAULT_ACCESS_TTL,
    DEFAULT_REFRESH_TTL,
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
)
from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from tulip_storage.models import Household, User, UserRole
from tulip_storage.models import Session as SessionRow
from tulip_storage.repositories import AuditLogWriter

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


router = APIRouter(prefix="/v1/auth", tags=["auth"])
log = structlog.get_logger("tulip_api.auth")


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    body: RegisterRequest,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
) -> RegisterResponse:
    """Create a new household and its first (admin) user."""
    household = Household(
        id=uuid4(),
        name=body.household_name,
        base_currency="USD",
    )
    session.add(household)
    session.flush()

    user = User(
        household_id=household.id,
        id=uuid4(),
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=UserRole.ADMIN,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered in this household",
        ) from exc

    AuditLogWriter(session, household.id).write(
        action="register",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        after={"email": user.email, "role": user.role.value},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info("user.registered", user_id=str(user.id), household_id=str(household.id))
    return RegisterResponse(user_id=user.id, household_id=household.id, role="admin")


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Verify credentials and issue an access + refresh token pair."""
    user = session.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        log.info("login.failed", email=body.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    return _issue_tokens(session, user, settings, request)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest,
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Exchange a refresh token for a fresh token pair (rotates refresh)."""
    rt_hash = hash_refresh_token(body.refresh_token)
    row = session.execute(
        select(SessionRow).where(SessionRow.refresh_token_hash == rt_hash)
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or _is_expired(row.expires_at):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    user = session.get(User, (row.household_id, row.user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    # Rotate: revoke this row, then issue a fresh pair.
    row.revoked_at = datetime.now(tz=UTC)
    session.flush()
    return _issue_tokens(session, user, settings, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    body: LogoutRequest,
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Revoke a refresh token. Subsequent uses are rejected."""
    rt_hash = hash_refresh_token(body.refresh_token)
    row = session.execute(
        select(SessionRow).where(SessionRow.refresh_token_hash == rt_hash)
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(tz=UTC)
        session.commit()


# ---- helpers ---------------------------------------------------------------


def _issue_tokens(
    session: Session, user: User, settings: Settings, request: Request
) -> TokenResponse:
    access = create_access_token(
        user_id=user.id,
        household_id=user.household_id,
        role=user.role.value,
        secret=settings.jwt_secret,
        ttl=DEFAULT_ACCESS_TTL,
    )
    refresh_plain = create_refresh_token()
    now = datetime.now(tz=UTC)
    session.add(
        SessionRow(
            id=uuid4(),
            household_id=user.household_id,
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_plain),
            created_at=now,
            expires_at=now + DEFAULT_REFRESH_TTL,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )

    AuditLogWriter(session, user.household_id).write(
        action="login",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info("user.login", user_id=str(user.id), household_id=str(user.household_id))

    return TokenResponse(
        access_token=access,
        refresh_token=refresh_plain,
        expires_in=int(DEFAULT_ACCESS_TTL.total_seconds()),
    )


def _request_uuid(request: Request) -> UUID | None:
    from uuid import UUID

    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _is_expired(expires_at: datetime) -> bool:
    """Compare expires_at to now(UTC), treating naive datetimes as UTC.

    SQLite drops timezone info on round-trip; production Postgres preserves
    it. This helper handles both.
    """
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(tz=UTC)
