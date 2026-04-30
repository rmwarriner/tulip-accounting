"""FastAPI dependencies for authenticated routes."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import Depends, Header

from tulip_api.auth.tokens import Claims, InvalidTokenError, verify_access_token
from tulip_api.config import Settings, get_settings
from tulip_api.errors import ForbiddenError, UnauthorizedError


def get_current_claims(
    authorization: str | None = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Claims:
    """Extract and verify the bearer token; return its Claims."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_access_token(token, secret=settings.jwt_secret)
    except InvalidTokenError as exc:
        raise UnauthorizedError(
            "The access token is invalid, expired, or has a bad signature. Sign in again."
        ) from exc


def require_role(*allowed: str) -> Callable[[Claims], Claims]:
    """Build a dependency that fails with 403 unless caller has one of `allowed` roles."""
    allowed_set = frozenset(allowed)

    def dep(claims: Claims = Depends(get_current_claims)) -> Claims:  # noqa: B008
        if claims.role not in allowed_set:
            raise ForbiddenError(
                f"This operation requires one of: {', '.join(sorted(allowed_set))}. "
                f"Your role is {claims.role!r}."
            )
        return claims

    return dep


def _allowed_to_write_account_role(claims: Claims, allowed: Iterable[str]) -> None:
    if claims.role not in set(allowed):
        raise ForbiddenError("Your role does not have write access to accounts.")
