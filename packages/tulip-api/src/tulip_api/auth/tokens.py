"""JWT access tokens and opaque refresh tokens.

Access tokens are short-lived (15 minutes by default), self-contained JWTs
signed with HS256. Refresh tokens are opaque random strings persisted as
SHA-256 hashes in the sessions table — never the plaintext.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError as PyJwtInvalidTokenError

DEFAULT_ACCESS_TTL: Final[timedelta] = timedelta(minutes=15)
DEFAULT_REFRESH_TTL: Final[timedelta] = timedelta(days=30)
DEFAULT_MFA_CHALLENGE_TTL: Final[timedelta] = timedelta(minutes=5)
JWT_ALGORITHM: Final[str] = "HS256"
ISSUER: Final[str] = "tulip-accounting"

#: ``purpose`` claim values. Access tokens carry no purpose for backward
#: compatibility; the MFA challenge JWT must carry this exact string.
PURPOSE_MFA_CHALLENGE: Final[str] = "mfa_challenge"


class InvalidTokenError(ValueError):
    """Raised on signature mismatch, expired token, or malformed JWT."""


@dataclass(frozen=True, slots=True)
class Claims:
    """Validated access-token payload."""

    user_id: UUID
    household_id: UUID
    role: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(
    *,
    user_id: UUID,
    household_id: UUID,
    role: str,
    secret: str,
    ttl: timedelta = DEFAULT_ACCESS_TTL,
) -> str:
    """Encode a JWT access token. Returns the compact (3-segment) form."""
    now = datetime.now(tz=UTC)
    exp = now + ttl
    payload: dict[str, object] = {
        "iss": ISSUER,
        "sub": str(user_id),
        "household_id": str(household_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str, *, secret: str) -> Claims:
    """Verify signature + expiry + issuer; return parsed Claims.

    Raises InvalidTokenError on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=ISSUER,
            options={"require": ["sub", "household_id", "role", "iat", "exp"]},
        )
    except PyJwtInvalidTokenError as exc:
        raise InvalidTokenError(str(exc)) from exc
    return Claims(
        user_id=UUID(payload["sub"]),
        household_id=UUID(payload["household_id"]),
        role=payload["role"],
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )


def create_refresh_token() -> str:
    """Return a 256-bit url-safe random refresh token."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(refresh_token: str) -> str:
    """Hash a refresh token for storage (SHA-256, hex)."""
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MfaChallengeClaims:
    """Validated MFA-challenge JWT payload."""

    user_id: UUID
    household_id: UUID
    jti: UUID
    issued_at: datetime
    expires_at: datetime


def create_mfa_challenge_token(
    *,
    user_id: UUID,
    household_id: UUID,
    secret: str,
    ttl: timedelta = DEFAULT_MFA_CHALLENGE_TTL,
) -> str:
    """Mint a short-lived JWT that authorizes a single MFA-step-2 attempt.

    Carries ``purpose: "mfa_challenge"`` so an access token cannot be used
    in its place (and vice versa). ``jti`` is a fresh UUIDv4 the caller
    must persist on first use so that replay attempts (e.g. the second
    half of a stolen request log) are rejected — see M-7 in #219.
    """
    now = datetime.now(tz=UTC)
    exp = now + ttl
    payload: dict[str, object] = {
        "iss": ISSUER,
        "sub": str(user_id),
        "household_id": str(household_id),
        "purpose": PURPOSE_MFA_CHALLENGE,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def verify_mfa_challenge_token(token: str, *, secret: str) -> MfaChallengeClaims:
    """Verify an MFA-challenge JWT.

    Raises :class:`InvalidTokenError` on signature mismatch, expiry,
    issuer mismatch, malformed ``jti``, or — critically — wrong
    ``purpose``. An access token submitted here is rejected. Single-use
    enforcement (rejecting the same ``jti`` twice) is the caller's
    responsibility; this function only parses + signature-checks.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=ISSUER,
            options={"require": ["sub", "household_id", "purpose", "jti", "iat", "exp"]},
        )
    except PyJwtInvalidTokenError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("purpose") != PURPOSE_MFA_CHALLENGE:
        raise InvalidTokenError("token is not an MFA challenge")
    try:
        jti = UUID(payload["jti"])
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError("jti is not a valid UUID") from exc
    return MfaChallengeClaims(
        user_id=UUID(payload["sub"]),
        household_id=UUID(payload["household_id"]),
        jti=jti,
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )
