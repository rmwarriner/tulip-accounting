"""POST /v1/auth/{register,login,refresh,logout}.

Audit log entries are written for every successful auth event. Failed
logins are intentionally NOT written here (would create a vector for
filling the audit log with garbage); they're emitted to the app log via
structlog instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from tulip_api.auth.deps import get_current_claims
from tulip_api.auth.mfa import (
    build_provisioning_uri,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_totp_secret,
    verify_totp_code,
)
from tulip_api.auth.passwords import hash_password, needs_rehash, verify_password
from tulip_api.auth.rate_limit import (
    AUTH_LOGIN_LIMIT,
    AUTH_LOGIN_MFA_LIMIT,
    AUTH_LOGIN_RECOVER_LIMIT,
    AUTH_MFA_ENROLL_LIMIT,
    AUTH_REFRESH_LIMIT,
    get_user_id_from_jwt,
    limiter,
)
from tulip_api.auth.recovery_codes import (
    generate_recovery_codes,
    hash_recovery_code,
    verify_recovery_code,
)
from tulip_api.auth.tokens import (
    DEFAULT_ACCESS_TTL,
    DEFAULT_MFA_CHALLENGE_TTL,
    DEFAULT_REFRESH_TTL,
    Claims,
    InvalidTokenError,
    create_access_token,
    create_mfa_challenge_token,
    create_refresh_token,
    hash_refresh_token,
    verify_mfa_challenge_token,
)
from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.errors import (
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidMfaTokenError,
    InvalidRefreshTokenError,
    MfaAlreadyEnrolledError,
    MfaEnrollmentRequiredError,
    MfaInvalidCodeError,
    MfaInvalidRecoveryCodeError,
    MfaNotEnrolledError,
    MfaNotPendingError,
    MfaRequiredError,
    UnauthorizedError,
    problem_response,
)
from tulip_api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MfaEnrollResponse,
    MfaLoginRequest,
    MfaRecoveryCodesResponse,
    MfaRecoveryLoginRequest,
    MfaRecoveryStatusResponse,
    MfaRegenerateRequest,
    MfaVerifyRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from tulip_storage.models import (
    AccountType,
    Household,
    MfaPolicy,
    MfaRecoveryCode,
    UsedMfaChallenge,
    User,
    UserRole,
)
from tulip_storage.models import Session as SessionRow
from tulip_storage.repositories import (
    AccountRepository,
    AllocationPoolRepository,
    AuditLogWriter,
    PeriodRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


router = APIRouter(prefix="/v1/auth", tags=["auth"])
log = structlog.get_logger("tulip_api.auth")

# A precomputed argon2 hash used only to make the no-such-email login path
# pay roughly the same wall-clock as a single-match path (#221). Lazily
# initialised so import-time stays cheap.
_TIMING_DEFENSE_DUMMY_HASH: str | None = None


def _timing_defense_dummy_hash() -> str:
    """Return a stable argon2 hash for the timing-defense dummy verify."""
    global _TIMING_DEFENSE_DUMMY_HASH
    if _TIMING_DEFENSE_DUMMY_HASH is None:
        # Hash of a constant string. The plaintext is never sent; this is
        # consumed only by verify_password() to spend argon2 cost.
        _TIMING_DEFENSE_DUMMY_HASH = hash_password("tulip-timing-defense-dummy")
    return _TIMING_DEFENSE_DUMMY_HASH


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: problem_response("auth.duplicate_email"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
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
        raise DuplicateEmailError() from exc

    # Seed a default current-year period so tests + first-time users can
    # immediately post transactions without explicitly creating one.
    today = date.today()
    PeriodRepository(session, household.id).create(
        start_date=date(today.year, 1, 1),
        end_date=date(today.year, 12, 31),
    )

    # Seed the three system pools (Inflow / Unallocated / Spent) for the
    # household's base currency. Other currencies are created lazily on
    # first use; see ADR-0001.
    AllocationPoolRepository(session, household.id).get_or_create_system_pools(
        currency=household.base_currency,
    )

    # Seed the Imbalance:Unknown EQUITY account so import-apply (P5.4.a)
    # has a target the default Categorizer (NullCategorizer) can resolve
    # without per-call account creation. Conventional accounting suspense
    # account; user re-categorizes via the transaction-edit API.
    AccountRepository(session, household.id).create(
        code="Imbalance:Unknown",
        name="Imbalance: Unknown",
        type=AccountType.EQUITY,
        currency=household.base_currency,
        created_by_user_id=user.id,
    )

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


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        401: problem_response("auth.invalid_credentials", "auth.mfa_required"),
        403: problem_response("auth.mfa_enrollment_required"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
        429: problem_response("auth.rate_limited"),
    },
)
@limiter.limit(AUTH_LOGIN_LIMIT)
def login(
    body: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Verify credentials and either issue tokens or trigger an MFA challenge.

    Outcomes (deciding in order):

    * Wrong credentials → 401 plain ``invalid credentials`` (will become
      ``auth.invalid_credentials`` in P2.x.2). Never reveals enrollment.
    * Caller is TOTP-enrolled → 401 ``auth.mfa_required`` carrying a
      short-lived ``mfa_token`` for ``/v1/auth/login/mfa``.
    * Household policy mandates MFA for the caller's role and they are
      not enrolled → 403 ``auth.mfa_enrollment_required``.
    * Otherwise → tokens (the pre-P2.x.1.b behavior).
    """
    # Email is unique per household, not globally — two households can
    # both have alice@example.com. On login we don't have a household
    # discriminator, so we authenticate against every user with that
    # email and pick the one whose password matches. Collisions across
    # households + matching passwords are extremely unlikely; if it ever
    # happens, we just sign in as the first match.
    #
    # #221 timing-oracle defense: iterate ALL candidates without short-
    # circuit so wall-clock doesn't distinguish "matched first" from
    # "matched last." When there are zero candidates, run one dummy
    # argon2 verify so the no-such-email response takes ~argon2 time too.
    # This still leaks multi-household-count (N candidates ⇒ N verifies),
    # but eliminates the existence + position oracles. Full defense would
    # require padding to a fixed verify-count or making emails globally
    # unique — both deferred decisions.
    candidates = session.execute(select(User).where(User.email == body.email)).scalars().all()
    user: User | None = None
    for candidate in candidates:
        if verify_password(body.password, candidate.password_hash) and user is None:
            user = candidate
    if user is None:
        if not candidates:
            # Force the no-such-email path to pay ~argon2 cost too.
            verify_password(body.password, _timing_defense_dummy_hash())
        else:
            # We had at least one matching email but the password didn't
            # match any of them. M-20 (#219): write an audit row anchored
            # on the first candidate so per-user enumeration / brute-force
            # patterns are reconstructable. No row for the truly unknown-
            # email case — there's no household_id to scope the row to,
            # and structlog already records the attempt below.
            first = candidates[0]
            AuditLogWriter(session, first.household_id).write(
                action="login_failed",
                actor_kind="user",
                actor_user_id=first.id,
                entity_type="user",
                entity_id=first.id,
                metadata={"email": body.email},
                request_id=_request_uuid(request),
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            session.commit()
        log.info("login.failed", email=body.email)
        raise InvalidCredentialsError()

    # Argon2 parameter upgrades: re-hash on next successful password verify.
    # Must commit here — /login/mfa + /login/recover don't see the plaintext.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        session.commit()
        log.info("user.password_rehashed", user_id=str(user.id))

    if user.totp_enrolled_at is not None:
        token = create_mfa_challenge_token(
            user_id=user.id,
            household_id=user.household_id,
            secret=settings.jwt_secret,
            ttl=DEFAULT_MFA_CHALLENGE_TTL,
        )
        log.info("user.login.mfa_challenge_issued", user_id=str(user.id))
        raise MfaRequiredError(
            mfa_token=token,
            expires_in=int(DEFAULT_MFA_CHALLENGE_TTL.total_seconds()),
        )

    household = session.get(Household, user.household_id)
    if household is not None and _enrollment_required(household.mfa_policy, user.role):
        log.info("user.login.enrollment_required", user_id=str(user.id))
        raise MfaEnrollmentRequiredError()

    return _issue_tokens(session, user, settings, request)


@router.post(
    "/login/mfa",
    response_model=TokenResponse,
    responses={
        401: problem_response("auth.invalid_mfa_token", "auth.mfa_invalid_code"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
        429: problem_response("auth.rate_limited"),
    },
)
@limiter.limit(AUTH_LOGIN_MFA_LIMIT)
def login_mfa(
    body: MfaLoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Complete a login that was gated by an MFA challenge.

    Verifies the short-lived ``mfa_token`` from step 1, validates the
    submitted TOTP code against the user's stored secret, and issues
    access + refresh tokens on success.
    """
    try:
        claims = verify_mfa_challenge_token(body.mfa_token, secret=settings.jwt_secret)
    except InvalidTokenError as exc:
        log.info("user.login.mfa_token_rejected", reason=str(exc))
        raise InvalidMfaTokenError() from exc
    if not _consume_mfa_challenge(session, claims.jti, claims.expires_at):
        log.info("user.login.mfa_token_replay", jti=str(claims.jti))
        raise InvalidMfaTokenError()

    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None or user.totp_secret_encrypted is None or user.totp_enrolled_at is None:
        # Token was valid but the user is no longer enrolled (or the row
        # vanished). Treat as a token-rejection rather than an MFA-code
        # error — there's nothing to verify against.
        session.commit()
        raise InvalidMfaTokenError()

    secret = decrypt_totp_secret(
        user.totp_secret_encrypted,
        master_key=settings.master_key,
        household_id=user.household_id,
        user_id=user.id,
    )
    if not verify_totp_code(secret, body.code):
        log.info("user.login.mfa_code_rejected", user_id=str(user.id))
        AuditLogWriter(session, user.household_id).write(
            action="mfa.code_rejected",
            actor_kind="user",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request_id=_request_uuid(request),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        session.commit()
        raise MfaInvalidCodeError()

    AuditLogWriter(session, user.household_id).write(
        action="login_mfa_success",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return _issue_tokens(session, user, settings, request)


def _enrollment_required(policy: MfaPolicy, role: UserRole) -> bool:
    """Decide whether a user must enroll in MFA before logging in."""
    if policy is MfaPolicy.REQUIRED_FOR_ALL:
        return True
    if policy is MfaPolicy.REQUIRED_FOR_ADMINS:
        return role is UserRole.ADMIN
    return False


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={
        401: problem_response("auth.invalid_refresh_token"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
        429: problem_response("auth.rate_limited"),
    },
)
@limiter.limit(AUTH_REFRESH_LIMIT)
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
        raise InvalidRefreshTokenError()

    user = session.get(User, (row.household_id, row.user_id))
    if user is None:
        raise InvalidRefreshTokenError()

    # Rotate: revoke this row, then issue a fresh pair.
    row.revoked_at = datetime.now(tz=UTC)
    AuditLogWriter(session, user.household_id).write(
        action="auth.refresh",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="session",
        entity_id=row.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.flush()
    return _issue_tokens(session, user, settings, request)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def logout(
    body: LogoutRequest,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Revoke a refresh token. Subsequent uses are rejected."""
    rt_hash = hash_refresh_token(body.refresh_token)
    row = session.execute(
        select(SessionRow).where(SessionRow.refresh_token_hash == rt_hash)
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(tz=UTC)
        AuditLogWriter(session, row.household_id).write(
            action="auth.logout",
            actor_kind="user",
            actor_user_id=row.user_id,
            entity_type="session",
            entity_id=row.id,
            request_id=_request_uuid(request),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        session.commit()


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized", "auth.invalid_credentials"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Rotate the caller's password and revoke their refresh tokens (#242).

    Verifies ``current_password`` against the stored Argon2id hash, then
    replaces the hash with a fresh one over ``new_password``. Every
    outstanding (non-revoked) session for the user is revoked — a stale
    refresh token shouldn't survive a credential rotation. The caller's
    bare access token still works for its remaining TTL; their next
    ``/v1/auth/refresh`` will need a fresh login.

    The audit row carries no password material — only the count of
    sessions revoked.
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UnauthorizedError("Your account no longer exists.")
    if not verify_password(body.current_password, user.password_hash):
        raise InvalidCredentialsError()

    user.password_hash = hash_password(body.new_password)

    now = datetime.now(tz=UTC)
    result = session.execute(
        update(SessionRow)
        .where(
            SessionRow.household_id == user.household_id,
            SessionRow.user_id == user.id,
            SessionRow.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    sessions_revoked = int(cast("CursorResult[Any]", result).rowcount or 0)

    AuditLogWriter(session, user.household_id).write(
        action="password_changed",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        metadata={"sessions_revoked": sessions_revoked},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info("user.password_changed", user_id=str(user.id), sessions_revoked=sessions_revoked)


@router.post(
    "/mfa/enroll",
    response_model=MfaEnrollResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        409: problem_response("auth.mfa_already_enrolled"),
        429: problem_response("auth.rate_limited"),
    },
)
@limiter.limit(AUTH_MFA_ENROLL_LIMIT, key_func=get_user_id_from_jwt)
def mfa_enroll(
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MfaEnrollResponse:
    """Start TOTP enrollment.

    Generates a fresh secret, persists it field-encrypted, and returns
    the plaintext + an ``otpauth://`` provisioning URI so the caller can
    render a QR code. The secret is not active until the caller proves
    possession via ``POST /v1/auth/mfa/verify``.

    Repeated calls before verification rotate the secret (intended — the
    user may lose the QR before scanning). After verification, this
    endpoint returns 409 ``auth.mfa_already_enrolled``.
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        # Token is valid but the user row vanished — treat as auth failure.
        raise UnauthorizedError("Your account no longer exists.")
    if user.totp_enrolled_at is not None:
        raise MfaAlreadyEnrolledError()

    secret = generate_totp_secret()
    user.totp_secret_encrypted = encrypt_totp_secret(
        secret,
        master_key=settings.master_key,
        household_id=user.household_id,
        user_id=user.id,
    )
    session.flush()

    AuditLogWriter(session, user.household_id).write(
        action="mfa.enroll",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info("user.mfa_enroll_started", user_id=str(user.id))

    return MfaEnrollResponse(
        secret=secret,
        provisioning_uri=build_provisioning_uri(secret=secret, email=user.email),
    )


@router.post(
    "/mfa/verify",
    response_model=MfaRecoveryCodesResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: problem_response("auth.mfa_not_pending", "request.body_invalid"),
        401: problem_response("auth.unauthorized", "auth.mfa_invalid_code"),
        409: problem_response("auth.mfa_already_enrolled"),
        422: problem_response("validation.failed"),
    },
)
def mfa_verify(
    body: MfaVerifyRequest,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MfaRecoveryCodesResponse:
    """Complete TOTP enrollment and mint single-use recovery codes.

    Returns 8 plaintext recovery codes — the only time they're ever
    visible. They're stored argon2id-hashed in ``mfa_recovery_codes``;
    each can be redeemed at most once via ``/v1/auth/login/recover``.

    Errors emitted as RFC 9457 Problem Details:
        * ``auth.mfa_not_pending`` (400) — no enrollment in progress.
        * ``auth.mfa_already_enrolled`` (409) — enrollment already active.
        * ``auth.mfa_invalid_code`` (401) — code did not match.
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UnauthorizedError("Your account no longer exists.")
    if user.totp_enrolled_at is not None:
        raise MfaAlreadyEnrolledError()
    if user.totp_secret_encrypted is None:
        raise MfaNotPendingError()

    secret = decrypt_totp_secret(
        user.totp_secret_encrypted,
        master_key=settings.master_key,
        household_id=user.household_id,
        user_id=user.id,
    )
    if not verify_totp_code(secret, body.code):
        log.info("user.mfa_verify_failed", user_id=str(user.id))
        raise MfaInvalidCodeError()

    user.totp_enrolled_at = datetime.now(tz=UTC)
    plaintext_codes = _mint_recovery_codes(session, user)
    session.flush()

    AuditLogWriter(session, user.household_id).write(
        action="mfa.verify",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    AuditLogWriter(session, user.household_id).write(
        action="mfa.recovery_codes_generated",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info("user.mfa_enrolled", user_id=str(user.id))
    return MfaRecoveryCodesResponse(recovery_codes=plaintext_codes)


@router.post(
    "/login/recover",
    response_model=TokenResponse,
    responses={
        401: problem_response("auth.invalid_mfa_token", "auth.mfa_invalid_recovery_code"),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
        429: problem_response("auth.rate_limited"),
    },
)
@limiter.limit(AUTH_LOGIN_RECOVER_LIMIT)
def login_recover(
    body: MfaRecoveryLoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Step-2 alternative to ``/login/mfa`` using a recovery code.

    Verifies the short-lived ``mfa_token`` from step 1, then matches the
    submitted ``recovery_code`` against the user's unused stored hashes.
    On a hit: marks the row used, audit-logs ``mfa.recovery_login``,
    issues access + refresh tokens. MFA stays enrolled — the user keeps
    their authenticator and the remaining codes — per design decision (2a).
    """
    try:
        claims = verify_mfa_challenge_token(body.mfa_token, secret=settings.jwt_secret)
    except InvalidTokenError as exc:
        log.info("user.login.recover_token_rejected", reason=str(exc))
        raise InvalidMfaTokenError() from exc
    if not _consume_mfa_challenge(session, claims.jti, claims.expires_at):
        log.info("user.login.recover_token_replay", jti=str(claims.jti))
        raise InvalidMfaTokenError()

    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None or user.totp_enrolled_at is None:
        session.commit()
        raise InvalidMfaTokenError()

    matched = _consume_recovery_code(session, user, body.recovery_code)
    if matched is None:
        log.info("user.login.recover_rejected", user_id=str(user.id))
        AuditLogWriter(session, user.household_id).write(
            action="mfa.recovery_rejected",
            actor_kind="user",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request_id=_request_uuid(request),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        session.commit()
        raise MfaInvalidRecoveryCodeError()

    AuditLogWriter(session, user.household_id).write(
        action="mfa.recovery_login",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        metadata={"recovery_code_id": str(matched.id)},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return _issue_tokens(session, user, settings, request)


@router.post(
    "/mfa/recovery-codes/regenerate",
    response_model=MfaRecoveryCodesResponse,
    responses={
        401: problem_response(
            "auth.unauthorized", "auth.mfa_invalid_code", "auth.mfa_not_enrolled"
        ),
        400: problem_response("request.body_invalid"),
        422: problem_response("validation.failed"),
    },
)
def mfa_regenerate_recovery_codes(
    body: MfaRegenerateRequest,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MfaRecoveryCodesResponse:
    """Invalidate existing recovery codes and mint a fresh set.

    Sensitive — requires both an access token *and* a current TOTP code
    (the "MFA-fresh" gate). A stale stolen access token is not enough.
    """
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None or user.totp_secret_encrypted is None or user.totp_enrolled_at is None:
        raise MfaNotEnrolledError()

    secret = decrypt_totp_secret(
        user.totp_secret_encrypted,
        master_key=settings.master_key,
        household_id=user.household_id,
        user_id=user.id,
    )
    if not verify_totp_code(secret, body.code):
        log.info("user.mfa_regenerate_failed", user_id=str(user.id))
        raise MfaInvalidCodeError()

    # Invalidate old set wholesale; mint new.
    session.execute(
        delete(MfaRecoveryCode).where(
            MfaRecoveryCode.household_id == user.household_id,
            MfaRecoveryCode.user_id == user.id,
        )
    )
    plaintext_codes = _mint_recovery_codes(session, user)

    AuditLogWriter(session, user.household_id).write(
        action="mfa.recovery_codes_regenerated",
        actor_kind="user",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info("user.mfa_recovery_codes_regenerated", user_id=str(user.id))
    return MfaRecoveryCodesResponse(recovery_codes=plaintext_codes)


@router.get(
    "/mfa/recovery-codes/status",
    response_model=MfaRecoveryStatusResponse,
    responses={401: problem_response("auth.unauthorized")},
)
def mfa_recovery_codes_status(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MfaRecoveryStatusResponse:
    """Return ``{remaining, total}`` for the caller's recovery codes.

    Never returns the codes themselves — the plaintext is shown only at
    ``/mfa/verify`` and ``/mfa/recovery-codes/regenerate``.
    """
    rows = (
        session.execute(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.household_id == claims.household_id,
                MfaRecoveryCode.user_id == claims.user_id,
            )
        )
        .scalars()
        .all()
    )
    return MfaRecoveryStatusResponse(
        remaining=sum(1 for r in rows if r.used_at is None),
        total=len(rows),
    )


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


def _consume_mfa_challenge(session: Session, jti: UUID, expires_at: datetime) -> bool:
    """Mark ``jti`` as redeemed; return False if it was already redeemed.

    M-7 (#219): single-use enforcement for the MFA-challenge JWT. The
    UNIQUE PK turns a replay into an IntegrityError. Committed eagerly
    so that a second attempt landing 50 ms later is rejected even if
    the first attempt's downstream verification fails.
    """
    session.add(UsedMfaChallenge(jti=jti, expires_at=expires_at))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    session.commit()
    return True


def _mint_recovery_codes(session: Session, user: User) -> list[str]:
    """Generate, store (hashed), and return plaintext recovery codes.

    Caller is responsible for committing the session.
    """
    plaintext = generate_recovery_codes()
    for code in plaintext:
        session.add(
            MfaRecoveryCode(
                id=uuid4(),
                household_id=user.household_id,
                user_id=user.id,
                code_hash=hash_recovery_code(code),
            )
        )
    return plaintext


def _consume_recovery_code(session: Session, user: User, submitted: str) -> MfaRecoveryCode | None:
    """Find an unused row whose hash matches ``submitted`` and mark it used.

    Returns the matched row on success, or ``None`` if no unused code
    matches. The caller is responsible for committing the session.
    """
    rows = (
        session.execute(
            select(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.household_id == user.household_id,
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.used_at.is_(None),
            )
            .order_by(MfaRecoveryCode.created_at)
        )
        .scalars()
        .all()
    )
    for row in rows:
        if verify_recovery_code(submitted, row.code_hash):
            row.used_at = datetime.now(tz=UTC)
            session.flush()
            return row
    return None
