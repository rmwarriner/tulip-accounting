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
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError as PyJwtInvalidTokenError

DEFAULT_ACCESS_TTL: Final[timedelta] = timedelta(minutes=15)
DEFAULT_REFRESH_TTL: Final[timedelta] = timedelta(days=30)
JWT_ALGORITHM: Final[str] = "HS256"
ISSUER: Final[str] = "tulip-accounting"


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
