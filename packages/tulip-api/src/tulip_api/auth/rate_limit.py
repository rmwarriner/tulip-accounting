"""Rate limiting for /v1/auth/* — credential-stuffing + MFA brute-force gate.

H-4 (#219): wires a single ``slowapi.Limiter`` into the FastAPI app and
declares per-route quotas on the auth endpoints most exposed to abuse:

* ``POST /v1/auth/login`` — primary credential-stuffing target.
* ``POST /v1/auth/login/mfa`` — TOTP brute force (6-digit code).
* ``POST /v1/auth/login/recover`` — recovery-code brute force.
* ``POST /v1/auth/refresh`` — token-rotation churn.

Limits are keyed on the client IP via :func:`get_remote_address`. The
audit (H-4) also called for a per-email key; we defer that — slowapi's
key function runs before the body is parsed, so layering email keying
on top of the IP gate requires a custom dependency that re-reads the
JSON body. The IP gate alone defeats the bulk-credential-stuffing case
this issue prioritises; per-email keying is tracked separately if the
threat model later demands it.

Exceedance is rendered as RFC 9457 Problem Details (``auth.rate_limited``)
so clients dispatch on it the same way as the rest of the API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from tulip_api.errors import PROBLEM_CONTENT_TYPE, TulipProblem

if TYPE_CHECKING:
    from fastapi import Request
    from slowapi.errors import RateLimitExceeded


#: Module-level limiter. Shared across the app. Storage backend defaults
#: to in-memory which is fine for single-process SQLite; switch to Redis
#: when we go multi-replica (deferred — see audit roadmap).
limiter = Limiter(key_func=get_remote_address)


#: Quotas applied to the four endpoints listed in the module docstring.
#: Tight enough to defeat bulk credential stuffing (~600/h per IP), loose
#: enough that a legitimate user who fat-fingers their password 4 times
#: still gets through.
AUTH_LOGIN_LIMIT = "10/minute"
AUTH_LOGIN_MFA_LIMIT = "10/minute"
AUTH_LOGIN_RECOVER_LIMIT = "10/minute"
AUTH_REFRESH_LIMIT = "30/minute"

#: Per-user quota on ``/v1/auth/mfa/enroll`` (security audit L-3, #350).
#: The endpoint generates a fresh TOTP secret + provisioning URI on every
#: call, rotating any unverified secret. A token-stealer with brief access
#: to a valid access token could spam enroll to deny the user re-enrollment
#: of their own MFA. 5 per 15 minutes leaves room for a confused user
#: who didn't scan the first QR code, blocks an automated attacker.
AUTH_MFA_ENROLL_LIMIT = "5/15minutes"


def get_user_id_from_jwt(request: Request) -> str:
    """Extract the user_id from the bearer token for per-user rate keying.

    Used as a slowapi ``key_func`` for endpoints already behind auth — the
    JWT's ``sub`` claim is the natural per-user discriminator. Falls back
    to the client IP when the header is missing/malformed/invalid; the
    endpoint's own auth dependency will then reject with 401, but slowapi
    has already accounted the attempt.
    """
    from tulip_api.auth.tokens import InvalidTokenError, verify_access_token
    from tulip_api.config import get_settings

    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        return get_remote_address(request)
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_access_token(token, secret=get_settings().jwt_secret)
    except InvalidTokenError:
        return get_remote_address(request)
    return f"user:{claims.user_id}"


class AuthRateLimitedError(TulipProblem):
    """The caller exceeded the per-IP auth rate limit (H-4, #219)."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        """Build the auth.rate_limited problem.

        ``retry_after_seconds`` is also emitted as a ``Retry-After``
        header per RFC 9110 §10.2.3.
        """
        super().__init__(
            code="auth.rate_limited",
            title="Too many auth attempts",
            status=429,
            detail=(
                "Too many authentication attempts from this client. "
                f"Try again in {retry_after_seconds} second(s)."
            ),
            extensions={"retry_after_seconds": retry_after_seconds},
            headers={"Retry-After": str(retry_after_seconds)},
        )


def auth_rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Render slowapi's ``RateLimitExceeded`` as Problem Details.

    slowapi's default handler returns a plain ``429`` with a string body,
    which would break our "every error is application/problem+json"
    contract. This wrapper extracts the retry window slowapi attaches to
    the exception (or 60s if unavailable) and emits the canonical
    ``auth.rate_limited`` body.
    """
    retry_after = getattr(exc, "retry_after", None)
    if not isinstance(retry_after, int) or retry_after <= 0:
        retry_after = 60
    problem = AuthRateLimitedError(retry_after_seconds=retry_after)
    body: dict[str, object] = {
        "type": problem.type_uri,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": request.url.path,
        "code": problem.code,
    }
    request_id = request.headers.get("x-request-id")
    if request_id:
        body["request_id"] = request_id
    for key, value in problem.extensions.items():
        body[key] = value
    return JSONResponse(
        status_code=problem.status,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=problem.headers,
    )
