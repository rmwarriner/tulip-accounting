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

    __slots__ = ("code", "detail", "extensions", "status", "title", "type_uri")

    def __init__(
        self,
        *,
        code: str,
        title: str,
        status: int,
        detail: str | None = None,
        type_uri: str | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        """Build a Problem Details exception. See class docstring for fields."""
        super().__init__(title)
        self.code = code
        self.title = title
        self.status = status
        self.detail = detail if detail is not None else title
        self.type_uri = type_uri or f"{DEFAULT_TYPE_PREFIX}/{code}"
        self.extensions: dict[str, Any] = dict(extensions) if extensions else {}


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


def install_problem_handlers(app: FastAPI) -> None:
    """Register the :class:`TulipProblem` handler on ``app``."""

    @app.exception_handler(TulipProblem)
    def _handle(request: Request, exc: TulipProblem) -> JSONResponse:
        return _render(request, exc)
