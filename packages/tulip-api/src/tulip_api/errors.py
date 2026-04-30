"""RFC 9457 (Problem Details for HTTP APIs) infrastructure.

Per ARCHITECTURE §7.8.2, every non-2xx response from ``tulip-api`` is
emitted as ``application/problem+json``.

This module provides three things:

* :class:`TulipProblem` — exception base. Carries ``code``, ``title``,
  ``status``, ``detail``, and optional ``extensions``. Domain modules
  raise concrete subclasses (or instances) at the boundary; the handler
  below renders them.
* :func:`install_problem_handlers` — wires the FastAPI exception handler
  that turns :class:`TulipProblem` into a Problem Details JSON response.
* The default ``type`` URI scheme — ``/.well-known/errors/<code>``.

The full migration of legacy ``HTTPException(detail=str)`` call sites to
this infra is tracked as P2.x.2; this module ships first because the MFA
endpoints in P2.x.1 are required to be RFC 9457 from day 1.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE: Final[str] = "application/problem+json"
DEFAULT_TYPE_PREFIX: Final[str] = "/.well-known/errors"


class TulipProblem(Exception):
    """An error that should be rendered as RFC 9457 Problem Details.

    ``code`` is the stable machine-readable identifier; clients dispatch
    on it. ``title`` is the short human-readable summary; ``detail`` is
    the per-occurrence explanation (and recovery hint, where one is
    computable). ``extensions`` are surfaced as additional top-level keys
    in the response body — never crammed into ``detail``.
    """

    __slots__ = ("code", "detail", "extensions", "headers", "status", "title", "type_uri")

    def __init__(
        self,
        *,
        code: str,
        title: str,
        status: int,
        detail: str | None = None,
        type_uri: str | None = None,
        extensions: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Build a Problem Details exception. See class docstring for fields."""
        super().__init__(title)
        self.code = code
        self.title = title
        self.status = status
        self.detail = detail if detail is not None else title
        self.type_uri = type_uri or f"{DEFAULT_TYPE_PREFIX}/{code}"
        self.extensions: dict[str, Any] = dict(extensions) if extensions else {}
        self.headers: dict[str, str] = dict(headers) if headers else {}


def _render(request: Request, problem: TulipProblem) -> JSONResponse:
    body: dict[str, Any] = {
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
    # Extension fields go at the top level (RFC 9457 §3.2). Reject keys
    # that would shadow required fields — safer than silently overwriting.
    reserved = {"type", "title", "status", "detail", "instance", "code", "request_id"}
    for key, value in problem.extensions.items():
        if key in reserved:
            raise ValueError(f"extension field {key!r} shadows reserved Problem Details field")
        body[key] = value
    return JSONResponse(
        status_code=problem.status,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=problem.headers or None,
    )


class UnauthorizedError(TulipProblem):
    """The request lacks valid authentication credentials.

    Covers: missing ``Authorization`` header, malformed bearer, expired
    token, invalid signature. Detail carries the specific reason; the
    ``code`` is a single ``auth.unauthorized`` so clients can dispatch
    on "any 401 means re-authenticate" without enumerating sub-cases.
    """

    def __init__(self, detail: str = "Authentication required.") -> None:
        """Build the auth.unauthorized problem with WWW-Authenticate per RFC 7235."""
        super().__init__(
            code="auth.unauthorized",
            title="Authentication required",
            status=401,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(TulipProblem):
    """The caller is authenticated but lacks permission for the operation."""

    def __init__(self, detail: str | None = None) -> None:
        """Build the auth.forbidden problem."""
        super().__init__(
            code="auth.forbidden",
            title="Forbidden",
            status=403,
            detail=detail or "Your account does not have permission to perform this operation.",
        )


class InvalidCredentialsError(TulipProblem):
    """Login was attempted with an unknown email or wrong password."""

    def __init__(self) -> None:
        """Build the auth.invalid_credentials problem.

        The body is intentionally identical for "no such user" and "wrong
        password" — never reveal which arm of the check failed.
        """
        super().__init__(
            code="auth.invalid_credentials",
            title="Invalid credentials",
            status=401,
            detail="The email or password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class DuplicateEmailError(TulipProblem):
    """Registration was attempted with an email that already exists in the household."""

    def __init__(self) -> None:
        """Build the auth.duplicate_email problem."""
        super().__init__(
            code="auth.duplicate_email",
            title="Email already registered",
            status=409,
            detail=(
                "An account with this email already exists in this household. "
                "Sign in with that account or use a different email."
            ),
        )


class InvalidRefreshTokenError(TulipProblem):
    """The refresh token is unknown, expired, or already revoked."""

    def __init__(self) -> None:
        """Build the auth.invalid_refresh_token problem."""
        super().__init__(
            code="auth.invalid_refresh_token",
            title="Invalid refresh token",
            status=401,
            detail="The refresh token is unknown, expired, or already revoked. Sign in again.",
        )


class InvalidMfaTokenError(TulipProblem):
    """The short-lived MFA challenge token is malformed, expired, or wrong-purpose."""

    def __init__(self) -> None:
        """Build the auth.invalid_mfa_token problem."""
        super().__init__(
            code="auth.invalid_mfa_token",
            title="Invalid MFA token",
            status=401,
            detail=(
                "The MFA challenge token is invalid or has expired. "
                "Sign in again to receive a fresh one."
            ),
        )


class MfaNotEnrolledError(TulipProblem):
    """An MFA-only operation was attempted by an account without active MFA."""

    def __init__(self) -> None:
        """Build the auth.mfa_not_enrolled problem."""
        super().__init__(
            code="auth.mfa_not_enrolled",
            title="MFA not enrolled",
            status=401,
            detail=(
                "This operation requires an active TOTP enrollment. "
                "Enroll via /v1/auth/mfa/enroll first."
            ),
            extensions={"enrollment_url": "/v1/auth/mfa/enroll"},
        )


class MfaAlreadyEnrolledError(TulipProblem):
    """The user has already completed MFA enrollment."""

    def __init__(self) -> None:
        """Build the auth.mfa_already_enrolled problem."""
        super().__init__(
            code="auth.mfa_already_enrolled",
            title="MFA already enrolled",
            status=409,
            detail=(
                "This account already has TOTP-based MFA active. "
                "Disable the existing enrollment before enrolling again."
            ),
        )


class MfaNotPendingError(TulipProblem):
    """Verify called with no enrollment in progress."""

    def __init__(self) -> None:
        """Build the auth.mfa_not_pending problem."""
        super().__init__(
            code="auth.mfa_not_pending",
            title="No MFA enrollment in progress",
            status=400,
            detail=(
                "There is no pending TOTP enrollment to verify. "
                "Call /v1/auth/mfa/enroll first to start enrollment."
            ),
        )


class MfaRequiredError(TulipProblem):
    """The caller must complete an MFA challenge before tokens are issued."""

    def __init__(self, *, mfa_token: str, expires_in: int) -> None:
        """Build the auth.mfa_required problem with flat top-level extensions."""
        super().__init__(
            code="auth.mfa_required",
            title="MFA required to complete login",
            status=401,
            detail=(
                "This account has TOTP-based MFA enabled. Submit the current "
                "6-digit code from your authenticator app along with the "
                "mfa_token below to /v1/auth/login/mfa to complete sign-in."
            ),
            extensions={
                "mfa_token": mfa_token,
                "mfa_token_expires_in": expires_in,
            },
        )


class MfaEnrollmentRequiredError(TulipProblem):
    """The caller must enroll in MFA before logging in (per household policy)."""

    def __init__(self) -> None:
        """Build the auth.mfa_enrollment_required problem."""
        super().__init__(
            code="auth.mfa_enrollment_required",
            title="MFA enrollment required",
            status=403,
            detail=(
                "This household requires MFA for accounts in your role. "
                "Visit the enrollment endpoint to set up an authenticator "
                "app, then sign in again."
            ),
            extensions={"enrollment_url": "/v1/auth/mfa/enroll"},
        )


class MfaInvalidCodeError(TulipProblem):
    """The TOTP code did not match the stored secret."""

    def __init__(self) -> None:
        """Build the auth.mfa_invalid_code problem."""
        super().__init__(
            code="auth.mfa_invalid_code",
            title="Invalid TOTP code",
            status=401,
            detail=(
                "The TOTP code did not match. Check that your authenticator "
                "app is showing the current 6-digit code and try again."
            ),
        )


class MfaInvalidRecoveryCodeError(TulipProblem):
    """The submitted recovery code didn't match an unused stored hash."""

    def __init__(self) -> None:
        """Build the auth.mfa_invalid_recovery_code problem."""
        super().__init__(
            code="auth.mfa_invalid_recovery_code",
            title="Invalid recovery code",
            status=401,
            detail=(
                "That recovery code is unknown or has already been used. "
                "Each recovery code can be used only once. If you have run "
                "out of codes, sign in with your authenticator app and "
                "regenerate a fresh set."
            ),
        )


def install_problem_handlers(app: FastAPI) -> None:
    """Register the :class:`TulipProblem` handler on ``app``."""

    @app.exception_handler(TulipProblem)
    def _handle(request: Request, exc: TulipProblem) -> JSONResponse:
        return _render(request, exc)
