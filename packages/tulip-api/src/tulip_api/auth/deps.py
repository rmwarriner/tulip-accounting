"""FastAPI dependencies for authenticated routes."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import Depends, Header, HTTPException, status

from tulip_api.auth.tokens import Claims, InvalidTokenError, verify_access_token
from tulip_api.config import Settings, get_settings


def get_current_claims(
    authorization: str | None = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Claims:
    """Extract and verify the bearer token; return its Claims."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_access_token(token, secret=settings.jwt_secret)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_role(*allowed: str) -> Callable[[Claims], Claims]:
    """Build a dependency that fails with 403 unless caller has one of `allowed` roles."""
    allowed_set = frozenset(allowed)

    def dep(claims: Claims = Depends(get_current_claims)) -> Claims:  # noqa: B008
        if claims.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {claims.role!r} not allowed (need one of {sorted(allowed_set)})",
            )
        return claims

    return dep


def _allowed_to_write_account_role(claims: Claims, allowed: Iterable[str]) -> None:
    if claims.role not in set(allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient role",
        )
