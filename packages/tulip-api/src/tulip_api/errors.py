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

Every router-layer error path raises a ``TulipProblem`` subclass; the
architecture test in ``tests/test_architecture_no_http_exception.py``
enforces that no source file under ``tulip_api/src/`` references
FastAPI's plain ``HTTPException`` (P2.x.2.c).
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

PROBLEM_CONTENT_TYPE: Final[str] = "application/problem+json"
DEFAULT_TYPE_PREFIX: Final[str] = "/.well-known/errors"


class ProblemDetailsResponse(BaseModel):
    """OpenAPI schema for an RFC 9457 ``application/problem+json`` body.

    Referenced from operation ``responses=`` blocks so the OpenAPI spec
    documents the error contract clients program against, and so
    schemathesis (P2.x.3) can validate that returned bodies conform.

    Extension fields (e.g. ``mfa_token``, ``enrollment_url``,
    ``retry_after_seconds``) appear at the top level per RFC 9457 §3.2;
    ``model_config = {"extra": "allow"}`` keeps the schema permissive.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="URI identifying the problem class.")
    title: str = Field(description="Short human-readable summary, stable per type.")
    status: int = Field(description="HTTP status code, mirrored in the body.")
    detail: str = Field(description="Per-occurrence explanation; recovery hint when computable.")
    instance: str = Field(description="URI of the specific failing request.")
    code: str = Field(description="Stable machine-readable error code (dotted segments).")
    request_id: str | None = Field(
        default=None,
        description="Request UUID stamped by RequestIdMiddleware; useful for support tickets.",
    )


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


class AccountNotFoundError(TulipProblem):
    """An account lookup either missed entirely or hit a row not visible to the caller."""

    def __init__(self) -> None:
        """Build the account.not_found problem."""
        super().__init__(
            code="account.not_found",
            title="Account not found",
            status=404,
            detail=(
                "No account with that ID exists in this household, or it is "
                "private to a member other than you."
            ),
        )


class AccountParentNotFoundError(TulipProblem):
    """The proposed parent account doesn't exist, isn't visible, or is inactive."""

    def __init__(self) -> None:
        """Build the account.parent_not_found problem."""
        super().__init__(
            code="account.parent_not_found",
            title="Parent account not found",
            status=404,
            detail=(
                "The parent_account_id either doesn't exist in this household, "
                "isn't visible to you, or has been deactivated."
            ),
        )


class AccountParentTypeMismatchError(TulipProblem):
    """A child account's type must match its parent's."""

    def __init__(self, *, child_type: str, parent_type: str) -> None:
        """Build the account.parent_type_mismatch problem."""
        super().__init__(
            code="account.parent_type_mismatch",
            title="Parent account type does not match",
            status=400,
            detail=(
                f"This account is a {child_type}; its parent is a {parent_type}. "
                "Children must share the parent's type. Pick a parent of the "
                "same type, or change this account's type before reparenting."
            ),
        )


class AccountParentCurrencyMismatchError(TulipProblem):
    """A child account's currency must match its parent's (#42; relaxation tracked in #44)."""

    def __init__(self, *, child_currency: str, parent_currency: str) -> None:
        """Build the account.parent_currency_mismatch problem."""
        super().__init__(
            code="account.parent_currency_mismatch",
            title="Parent account currency does not match",
            status=400,
            detail=(
                f"This account is denominated in {child_currency}; its parent "
                f"is in {parent_currency}. Children must share the parent's "
                "currency. Multi-currency hierarchies are tracked separately."
            ),
        )


class AccountParentVisibilityViolationError(TulipProblem):
    """A shared child cannot live under a private parent."""

    def __init__(self) -> None:
        """Build the account.parent_visibility_violation problem."""
        super().__init__(
            code="account.parent_visibility_violation",
            title="Parent visibility forbids this child",
            status=400,
            detail=(
                "A shared account cannot be a child of a private one — the "
                "child would be visible while its parent is hidden. Either "
                "make this account private, or pick a shared parent."
            ),
        )


class AccountParentCycleError(TulipProblem):
    """Reparenting would create a cycle (parent is a descendant of this account)."""

    def __init__(self) -> None:
        """Build the account.parent_cycle problem."""
        super().__init__(
            code="account.parent_cycle",
            title="Parent change would create a cycle",
            status=400,
            detail=(
                "The proposed parent is a descendant of this account; "
                "applying the change would create a cycle in the account tree."
            ),
        )


class AccountUnknownError(TulipProblem):
    """A transaction posting referenced an account that doesn't exist in this household."""

    def __init__(self, account_id: str) -> None:
        """Build the account.unknown problem.

        ``account_id`` is included in ``detail`` so the user can identify
        which posting was at fault.
        """
        super().__init__(
            code="account.unknown",
            title="Unknown account in posting",
            status=400,
            detail=(
                f"Posting references account {account_id}, which does not "
                "exist in this household. Check the account ID and resubmit."
            ),
        )


class TransactionInvalidError(TulipProblem):
    """A transaction failed domain-level validation (e.g. empty postings, bad shape)."""

    def __init__(self, reason: str) -> None:
        """Build the transaction.invalid problem.

        ``reason`` is the underlying validation message from
        ``tulip-core`` and is surfaced verbatim in ``detail``.
        """
        super().__init__(
            code="transaction.invalid",
            title="Invalid transaction",
            status=400,
            detail=reason,
        )


class TransactionUnbalancedError(TulipProblem):
    """A transaction's postings don't sum to zero per currency."""

    def __init__(self, reason: str) -> None:
        """Build the transaction.unbalanced problem (per ARCHITECTURE §7.8)."""
        super().__init__(
            code="transaction.unbalanced",
            title="Transaction does not balance",
            status=400,
            detail=reason,
        )


class PeriodClosedError(TulipProblem):
    """A write was attempted against a soft-closed period."""

    def __init__(self, reason: str) -> None:
        """Build the period.closed problem (per ARCHITECTURE §7.8)."""
        super().__init__(
            code="period.closed",
            title="Period is closed",
            status=400,
            detail=reason,
        )


class TransactionNotFoundError(TulipProblem):
    """A transaction lookup either missed or hit a row not in this household."""

    def __init__(self) -> None:
        """Build the transaction.not_found problem."""
        super().__init__(
            code="transaction.not_found",
            title="Transaction not found",
            status=404,
            detail="No transaction with that ID exists in this household.",
        )


class TransactionNotEditableError(TulipProblem):
    """PATCH was attempted on a non-PENDING transaction (P5.0)."""

    def __init__(self) -> None:
        """Build the transaction.not_editable problem."""
        super().__init__(
            code="transaction.not_editable",
            title="Transaction is not editable",
            status=409,
            detail=(
                "Only PENDING transactions can be edited. To change a posted "
                "transaction, void it (POST /v1/transactions/{id}/void) and "
                "create a corrected entry."
            ),
        )


class TransactionNotDeletableError(TulipProblem):
    """DELETE was attempted on a non-PENDING transaction (P5.0)."""

    def __init__(self) -> None:
        """Build the transaction.not_deletable problem."""
        super().__init__(
            code="transaction.not_deletable",
            title="Transaction is not deletable",
            status=409,
            detail=(
                "Only PENDING transactions can be hard-deleted. Posted "
                "transactions must be voided (POST /v1/transactions/{id}/void) "
                "to preserve the audit trail."
            ),
        )


class TransactionAlreadyVoidedError(TulipProblem):
    """A void was attempted on a transaction that is already voided (P5.0)."""

    def __init__(self, voided_by_transaction_id: str) -> None:
        """Build the transaction.already_voided problem.

        ``voided_by_transaction_id`` is surfaced as a Problem extension so
        clients can fetch the existing reversal without a second query.
        """
        super().__init__(
            code="transaction.already_voided",
            title="Transaction already voided",
            status=409,
            detail=(
                "This transaction has already been voided. The reversal "
                "transaction is referenced via the voided_by_transaction_id "
                "extension."
            ),
            extensions={"voided_by_transaction_id": voided_by_transaction_id},
        )


class TransactionNotVoidableError(TulipProblem):
    """A void was attempted on a transaction that isn't POSTED / RECONCILED (P5.0)."""

    def __init__(self, status: str) -> None:
        """Build the transaction.not_voidable problem.

        Surfaces the offending transaction's status as a Problem extension
        so the client can route to the correct corrective action (DELETE
        for PENDING, un-reconcile-then-void for RECONCILED in P5.1+).
        """
        super().__init__(
            code="transaction.not_voidable",
            title="Transaction is not in a voidable state",
            status=409,
            detail=(
                "Only POSTED transactions can be voided in this slice. "
                "PENDING transactions should be hard-deleted with DELETE; "
                "RECONCILED transactions need to be un-reconciled first "
                "(coming in P5.1+)."
            ),
            extensions={"status": status},
        )


class PoolNotFoundError(TulipProblem):
    """A posting carries a pool_id that doesn't exist in this household."""

    def __init__(self, pool_id: str) -> None:
        """Build the pool.not_found problem.

        ``pool_id`` is included in ``detail`` so the user can identify the
        offending posting. Visibility is uniform across all household pools
        for now — there's no information leak from echoing the bad UUID.
        """
        super().__init__(
            code="pool.not_found",
            title="Unknown pool in posting",
            status=400,
            detail=(
                f"Posting references pool {pool_id}, which does not exist "
                "in this household. Check the pool ID and resubmit."
            ),
        )


class PoolInactiveError(TulipProblem):
    """A posting's pool_id resolves to a deactivated pool."""

    def __init__(self, pool_id: str) -> None:
        """Build the pool.inactive problem."""
        super().__init__(
            code="pool.inactive",
            title="Pool is deactivated",
            status=400,
            detail=(
                f"Pool {pool_id} is deactivated and cannot accept new "
                "postings. Reactivate it or remove the pool_id from the "
                "posting and resubmit."
            ),
        )


class PoolCurrencyMismatchError(TulipProblem):
    """A posting's currency does not match its pool's currency."""

    def __init__(self, *, pool_id: str, pool_currency: str, posting_currency: str) -> None:
        """Build the pool.currency_mismatch problem."""
        super().__init__(
            code="pool.currency_mismatch",
            title="Pool currency does not match posting currency",
            status=400,
            detail=(
                f"Pool {pool_id} is denominated in {pool_currency}, but the "
                f"posting is in {posting_currency}. Pool tagging requires "
                "the pool and the posting to share a currency."
            ),
        )


class PoolInvalidAccountTypePairingError(TulipProblem):
    """A posting carries pool_id but its account type forbids pairing.

    v1 permits pool-tagging on EXPENSE accounts only.
    """

    def __init__(self, *, account_type: str) -> None:
        """Build the pool.invalid_account_type_pairing problem."""
        super().__init__(
            code="pool.invalid_account_type_pairing",
            title="Pool tagging not permitted for this account type",
            status=400,
            detail=(
                f"This posting is on a {account_type} account; only EXPENSE "
                "accounts may carry pool_id in v1. Remove the pool_id and "
                "resubmit, or move the posting to an expense account."
            ),
        )


class ShadowLedgerInternalError(TulipProblem):
    """The auto-paired shadow tx failed an internal invariant.

    Defense-in-depth: the engine's checks should catch every malformed
    pairing case before it gets here. Reaching this branch indicates a
    Tulip bug, not a user error. The body deliberately surfaces no
    detail beyond a request-id correlation hint.
    """

    def __init__(self) -> None:
        """Build the pool.shadow_unbalanced problem."""
        super().__init__(
            code="pool.shadow_unbalanced",
            title="Shadow-ledger pairing failed",
            status=500,
            detail=(
                "The server could not derive a valid shadow-ledger "
                "transaction for this main transaction. Please report "
                "this incident with the request_id."
            ),
        )


class EnvelopeNotFoundError(TulipProblem):
    """An envelope lookup either missed or hit a row not visible in this household."""

    def __init__(self) -> None:
        """Build the envelope.not_found problem."""
        super().__init__(
            code="envelope.not_found",
            title="Envelope not found",
            status=404,
            detail="No envelope with that ID exists in this household.",
        )


class SinkingFundNotFoundError(TulipProblem):
    """A sinking-fund lookup either missed or hit a row not visible in this household."""

    def __init__(self) -> None:
        """Build the sinking_fund.not_found problem."""
        super().__init__(
            code="sinking_fund.not_found",
            title="Sinking fund not found",
            status=404,
            detail="No sinking fund with that ID exists in this household.",
        )


class PoolTransferSamePoolError(TulipProblem):
    """A transfer was requested with identical source and destination pools."""

    def __init__(self) -> None:
        """Build the pool.transfer_same_pool problem."""
        super().__init__(
            code="pool.transfer_same_pool",
            title="Source and destination pools must differ",
            status=400,
            detail=(
                "A pool-to-pool transfer needs two distinct pools. "
                "Pick a different destination and resubmit."
            ),
        )


class PoolTransferCurrencyMismatchError(TulipProblem):
    """A transfer was requested across pools of different currencies."""

    def __init__(self, *, src_currency: str, dest_currency: str) -> None:
        """Build the pool.transfer_currency_mismatch problem."""
        super().__init__(
            code="pool.transfer_currency_mismatch",
            title="Pool currencies must match for a transfer",
            status=400,
            detail=(
                f"Source pool is {src_currency}; destination is {dest_currency}. "
                "Cross-currency pool transfers are not supported in v1; use "
                "a budget-inflow declaration in the destination's currency "
                "instead."
            ),
        )


class PoolTransferSystemPoolForbiddenError(TulipProblem):
    """A transfer was requested with a system pool as source or destination.

    System pools (Inflow / Unallocated / Spent) are plumbing for the
    shadow ledger; users move money in via ``budget-inflow`` and out via
    ``refill``. Direct transfers to/from system pools would skip the
    intent-recording semantics those endpoints provide.
    """

    def __init__(self, *, role: str) -> None:
        """Build the pool.transfer_system_pool_forbidden problem.

        ``role`` is "source" or "destination" — surfaced as an extension
        so clients can localize the message and highlight the right field.
        """
        super().__init__(
            code="pool.transfer_system_pool_forbidden",
            title="System pools cannot be transferred to or from",
            status=400,
            detail=(
                f"The {role} pool is a system pool. Use budget-inflow to add "
                "money to your budget and refill to fund an envelope; pool-to-pool "
                "transfers operate between user pools only."
            ),
            extensions={"role": role},
        )


class RefillScheduleNotFoundError(TulipProblem):
    """The envelope has no active refill schedule."""

    def __init__(self) -> None:
        """Build the refill_schedule.not_found problem."""
        super().__init__(
            code="refill_schedule.not_found",
            title="Refill schedule not found",
            status=404,
            detail=(
                "This envelope has no active refill schedule. POST a "
                "schedule to /v1/envelopes/{id}/refill-schedule first."
            ),
        )


class RefillScheduleEnvelopeHasNoRefillRuleError(TulipProblem):
    """Schedule was requested for an envelope without a refill_rule.

    A scheduled refill needs a rule to evaluate at fire time. Set the
    envelope's ``refill_rule`` via PATCH first, then schedule.
    """

    def __init__(self) -> None:
        """Build the refill_schedule.envelope_has_no_refill_rule problem."""
        super().__init__(
            code="refill_schedule.envelope_has_no_refill_rule",
            title="Envelope has no refill rule to schedule",
            status=400,
            detail=(
                "Set the envelope's refill_rule via "
                "PATCH /v1/envelopes/{id} before scheduling refills."
            ),
        )


class RefillScheduleInvalidRRuleError(TulipProblem):
    """The RRULE string couldn't be parsed by python-dateutil."""

    def __init__(self, *, reason: str) -> None:
        """Build the refill_schedule.invalid_rrule problem."""
        super().__init__(
            code="refill_schedule.invalid_rrule",
            title="Invalid RRULE",
            status=400,
            detail=(
                f"Could not parse the RRULE string: {reason}. "
                "Use RFC 5545 syntax, e.g. 'FREQ=MONTHLY;BYMONTHDAY=1'."
            ),
        )


class RefillScheduleAlreadyExistsError(TulipProblem):
    """A schedule already exists for this envelope.

    The unique partial index on ``(household_id, kind, idempotency_key)``
    enforces one schedule per envelope. DELETE the existing one before
    creating a replacement.
    """

    def __init__(self) -> None:
        """Build the refill_schedule.already_exists problem."""
        super().__init__(
            code="refill_schedule.already_exists",
            title="Refill schedule already exists for this envelope",
            status=409,
            detail=(
                "DELETE /v1/envelopes/{id}/refill-schedule first, then "
                "POST a new schedule. v1 supports one schedule per envelope."
            ),
        )


class PoolInflowCurrencyUnknownError(TulipProblem):
    """A budget-inflow request named a currency not in ISO 4217."""

    def __init__(self, *, currency: str) -> None:
        """Build the pool.inflow_currency_unknown problem."""
        super().__init__(
            code="pool.inflow_currency_unknown",
            title="Unknown currency for budget inflow",
            status=400,
            detail=(
                f"Currency {currency!r} is not a recognized ISO 4217 code. "
                "Check the currency code and resubmit."
            ),
        )


class ValidationFailedError(TulipProblem):
    """FastAPI / Pydantic input validation rejected the request body or params."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Build the validation.failed problem.

        ``errors`` is the list returned by FastAPI's ``RequestValidationError.errors()``.
        It's surfaced verbatim under the ``errors`` extension field so clients
        can localize the failures to specific fields.
        """
        super().__init__(
            code="validation.failed",
            title="Request validation failed",
            status=422,
            detail="One or more fields in the request body or query parameters are invalid.",
            extensions={"errors": errors},
        )


def _sanitize_for_json(value: Any) -> Any:  # noqa: ANN401 — Pydantic errors are heterogeneous
    """Recursively coerce values to JSON-safe primitives.

    Pydantic's error contexts include ``Decimal`` values for numeric
    constraints. Bytes occasionally show up in URL parsing errors. Coerce
    both to strings so the validation 422 response can render.
    """
    from decimal import Decimal as _Dec  # local to avoid top-of-file import bloat

    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, _Dec):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def problem_response(*codes: str) -> dict[str, Any]:
    """Build a FastAPI ``responses=`` value for a Problem Details error.

    Use as::

        @router.post("/login", responses={
            401: problem_response("auth.invalid_credentials", "auth.mfa_required"),
            403: problem_response("auth.mfa_enrollment_required"),
        })

    The list of codes is purely for the OpenAPI ``description``; the
    response body conforms to :class:`ProblemDetailsResponse` regardless.
    """
    return {
        "model": ProblemDetailsResponse,
        "content": {PROBLEM_CONTENT_TYPE: {}},
        "description": "; ".join(codes) if codes else "Problem Details error.",
    }


#: Default ``responses`` entries every body-accepting operation should carry.
#: Documents the framework-level errors (400 bad body, 422 validation) that
#: schemathesis can trigger with random inputs. Spread into a route's
#: ``responses=``: ``{**FRAMEWORK_BODY_RESPONSES, 401: ..., ...}``.
FRAMEWORK_BODY_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    400: problem_response("request.body_invalid"),
    422: problem_response("validation.failed"),
}


_FRAMEWORK_ERROR_CODES: Final[dict[int, str]] = {
    400: "request.body_invalid",
    404: "request.not_found",
    405: "request.method_not_allowed",
    415: "request.unsupported_media_type",
}


class ImportDuplicateFileError(TulipProblem):
    """A statement file with this content hash was already imported (P5.2.a).

    Idempotency per ADR-0004 §Q6: SHA-256 of the file bytes is the dedup
    key. Surfaces the existing batch id as an extension so clients can
    GET it without a second request.
    """

    def __init__(self, *, content_hash: str, existing_batch_id: str) -> None:
        """Build the import.duplicate_file problem (409)."""
        super().__init__(
            code="import.duplicate_file",
            title="Import file is a duplicate",
            status=409,
            detail=(
                "A statement file with the same SHA-256 hash has already been "
                "imported for this account. Re-importing the same file is "
                "blocked to preserve idempotency."
            ),
            extensions={
                "content_hash": content_hash,
                "existing_batch_id": existing_batch_id,
            },
        )


class ImportOfxParseFailedError(TulipProblem):
    """The uploaded bytes could not be parsed as OFX (P5.2.a)."""

    def __init__(self, *, reason: str) -> None:
        """Build the import.ofx_parse_failed problem (400)."""
        super().__init__(
            code="import.ofx_parse_failed",
            title="OFX file could not be parsed",
            status=400,
            detail=reason,
        )


class ImportQifParseFailedError(TulipProblem):
    """The uploaded bytes could not be parsed as QIF (P5.2.b)."""

    def __init__(self, *, reason: str) -> None:
        """Build the import.qif_parse_failed problem (400)."""
        super().__init__(
            code="import.qif_parse_failed",
            title="QIF file could not be parsed",
            status=400,
            detail=reason,
        )


class ImportCsvParseFailedError(TulipProblem):
    """The uploaded bytes could not be parsed as CSV per the supplied profile (P5.2.c)."""

    def __init__(self, *, reason: str) -> None:
        """Build the import.csv_parse_failed problem (400)."""
        super().__init__(
            code="import.csv_parse_failed",
            title="CSV file could not be parsed",
            status=400,
            detail=reason,
        )


class ImportCsvProfileMissingError(TulipProblem):
    """``source_format=csv`` requires a ``profile_id`` form field (P5.2.c)."""

    def __init__(self) -> None:
        """Build the import.csv_profile_missing problem (400)."""
        super().__init__(
            code="import.csv_profile_missing",
            title="CSV import requires a profile",
            status=400,
            detail=(
                "source_format='csv' requires the 'profile_id' form field. "
                "Create a profile via POST /v1/imports/profiles first, then "
                "pass its UUID."
            ),
        )


class CsvProfileNotFoundError(TulipProblem):
    """No CSV profile with that id or name exists in this household (P5.2.c)."""

    def __init__(self) -> None:
        """Build the csv_profile.not_found problem (404)."""
        super().__init__(
            code="csv_profile.not_found",
            title="CSV profile not found",
            status=404,
            detail="No CSV profile with that id or name exists in this household.",
        )


class CsvProfileDuplicateNameError(TulipProblem):
    """A CSV profile with the same name already exists in the household (P5.2.c)."""

    def __init__(self, *, name: str) -> None:
        """Build the csv_profile.duplicate_name problem (409)."""
        super().__init__(
            code="csv_profile.duplicate_name",
            title="CSV profile name already in use",
            status=409,
            detail=(
                f"A CSV profile named {name!r} already exists in this "
                "household. Names must be unique per household; pick "
                "another, or PATCH the existing profile."
            ),
            extensions={"name": name},
        )


class CsvProfileInvalidYamlError(TulipProblem):
    """The supplied YAML couldn't be parsed (or contained unsafe tags) (P5.2.c)."""

    def __init__(self, *, reason: str) -> None:
        """Build the csv_profile.invalid_yaml problem (400)."""
        super().__init__(
            code="csv_profile.invalid_yaml",
            title="CSV profile YAML is invalid",
            status=400,
            detail=reason,
        )


class ImportUnsupportedFormatError(TulipProblem):
    """The requested ``source_format`` isn't implemented yet."""

    def __init__(self, *, format_name: str, supported: tuple[str, ...]) -> None:
        """Build the import.unsupported_format problem (400)."""
        super().__init__(
            code="import.unsupported_format",
            title="Unsupported import format",
            status=400,
            detail=(
                f"source_format={format_name!r} is not yet implemented. "
                f"Supported formats: {', '.join(supported)}."
            ),
            extensions={"format": format_name, "supported": list(supported)},
        )


class ImportBatchNotFoundError(TulipProblem):
    """No import batch with that ID exists in this household (P5.2.a)."""

    def __init__(self) -> None:
        """Build the import_batch.not_found problem (404)."""
        super().__init__(
            code="import_batch.not_found",
            title="Import batch not found",
            status=404,
            detail="No import batch with that ID exists in this household.",
        )


class ImportAlreadyAppliedError(TulipProblem):
    """The import batch has already been applied to the ledger (P5.4.a)."""

    def __init__(self, *, batch_id: str) -> None:
        """Build the import.already_applied problem (409)."""
        super().__init__(
            code="import.already_applied",
            title="Import already applied",
            status=409,
            detail=(
                "This import batch has already been applied. To re-promote a "
                "specific line, use POST /v1/imports/{batch_id}/lines/{line_id}/promote."
            ),
            extensions={"batch_id": batch_id},
        )


class StatementLineNotFoundError(TulipProblem):
    """No statement line with that ID exists in this household / batch (P5.4.a)."""

    def __init__(self) -> None:
        """Build the import.line.not_found problem (404)."""
        super().__init__(
            code="import.line.not_found",
            title="Statement line not found",
            status=404,
            detail="No statement line with that ID exists in this batch.",
        )


class StatementLineAlreadyPromotedError(TulipProblem):
    """The statement line has already been promoted to a ledger tx (P5.4.a)."""

    def __init__(self, *, line_id: str, transaction_id: str) -> None:
        """Build the import.line.already_promoted problem (409)."""
        super().__init__(
            code="import.line.already_promoted",
            title="Statement line already promoted",
            status=409,
            detail=(
                "This statement line was already promoted to a ledger transaction. "
                "Edit the existing transaction instead."
            ),
            extensions={"line_id": line_id, "transaction_id": transaction_id},
        )


class StatementLineExcludedError(TulipProblem):
    """The statement line is excluded; un-exclude before promoting (P5.4.a)."""

    def __init__(self, *, line_id: str) -> None:
        """Build the import.line.excluded problem (422)."""
        super().__init__(
            code="import.line.excluded",
            title="Statement line excluded",
            status=422,
            detail=(
                "This statement line is excluded from import. Un-exclude it "
                "before attempting to promote it to the ledger."
            ),
            extensions={"line_id": line_id},
        )


class ImportCategorizeUnknownAccountError(TulipProblem):
    """The categorizer suggested an account_code with no matching Account (P5.4.a)."""

    def __init__(self, *, account_code: str) -> None:
        """Build the import.categorize.unknown_account problem (409)."""
        super().__init__(
            code="import.categorize.unknown_account",
            title="Categorizer suggested an unknown account",
            status=409,
            detail=(
                f"The categorizer suggested account_code={account_code!r}, but "
                "no account with that code exists in this household. Create the "
                "account first, then retry the apply / promote."
            ),
            extensions={"account_code": account_code},
        )


class RequestPayloadTooLargeError(TulipProblem):
    """The uploaded payload exceeds ``MAX_OFX_BYTES`` (P5.2.a).

    A size cap defends against accidental or malicious uploads that would
    OOM the handler when it slurps the whole upload into memory. The cap
    is currently a module constant; a follow-up will plumb it through
    ``Settings`` for per-deployment configurability.
    """

    def __init__(self, *, max_bytes: int) -> None:
        """Build the request.payload_too_large problem (413)."""
        super().__init__(
            code="request.payload_too_large",
            title="Upload too large",
            status=413,
            detail=(
                f"The uploaded file exceeds the {max_bytes}-byte limit. Real "
                "bank statements are typically under 1 MB; if you genuinely "
                "need to upload more, contact the operator to raise the cap."
            ),
            extensions={"max_bytes": max_bytes},
        )


class UnsupportedMediaTypeError(TulipProblem):
    """The uploaded file's content type isn't accepted by the endpoint."""

    def __init__(self, *, accepted: tuple[str, ...], received: str) -> None:
        """Build the request.unsupported_media_type problem (415)."""
        super().__init__(
            code="request.unsupported_media_type",
            title="Unsupported media type",
            status=415,
            detail=(
                f"The uploaded file's content_type {received!r} is not in the "
                f"accepted set: {', '.join(repr(a) for a in accepted)}."
            ),
            extensions={"accepted": list(accepted), "received": received},
        )


class InternalServerError(TulipProblem):
    """Catch-all for an unhandled exception escaping a route handler.

    The detail is deliberately generic — exception messages and
    tracebacks belong in server logs, not in HTTP responses. Clients see
    a stable ``server.internal_error`` code and a request_id (when one
    was supplied) for support correlation.
    """

    def __init__(self) -> None:
        """Build the server.internal_error problem (no exception text leaked)."""
        super().__init__(
            code="server.internal_error",
            title="Internal server error",
            status=500,
            detail=(
                "An unexpected error occurred on the server. If you can reproduce "
                "this, the request_id below identifies the failing request in the "
                "server logs."
            ),
        )


def install_problem_handlers(app: FastAPI) -> None:
    """Register the :class:`TulipProblem` handler on ``app``.

    Wires up four handlers, in order of specificity (Starlette dispatches
    by MRO, picking the most specific match):

    * :class:`TulipProblem` — typed domain errors raised by route code.
    * :class:`fastapi.exceptions.RequestValidationError` — Pydantic 422.
    * :class:`starlette.exceptions.HTTPException` — framework-level
      400 (malformed body), 404 (no route), 405 (wrong method), 415, etc.
    * :class:`Exception` — last-resort catch-all so an unhandled
      exception never escapes as the default ``text/plain`` 500.

    With all four registered, every non-2xx response is RFC 9457
    ``application/problem+json``. Schemathesis's contract test asserts
    this for documented endpoints; the catch-all closes the
    "unhandled-exception" gap that schemathesis can't see (production
    routes don't declare ``500`` because that's not a normal client
    response — it's a server bug).
    """
    import structlog
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    log = structlog.get_logger("tulip_api.errors")

    @app.exception_handler(TulipProblem)
    def _handle(request: Request, exc: TulipProblem) -> JSONResponse:
        return _render(request, exc)

    @app.exception_handler(RequestValidationError)
    def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's error structure can contain Decimal values (e.g. inside
        # ``ctx`` for ``ge`` / ``gt`` / ``le`` / ``lt`` constraints) that
        # JSONResponse can't serialize. Coerce them to strings recursively.
        sanitized = [_sanitize_for_json(e) for e in exc.errors()]
        return _render(request, ValidationFailedError(errors=sanitized))

    @app.exception_handler(Exception)
    def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the full exception (with traceback, via exc_info) under
        # the request's structlog context so operators can find it.
        # Critically, do NOT include the exception text in the response
        # body — that's the principle in ARCHITECTURE.md §1.1.7
        # (no internal identifiers in user-facing copy).
        log.exception(
            "internal_error",
            exc_info=exc,
            exc_type=type(exc).__name__,
            path=request.url.path,
        )
        return _render(request, InternalServerError())

    @app.exception_handler(StarletteHTTPException)
    def _handle_starlette(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Catches the framework-level errors that bypass our typed
        # exceptions: malformed body JSON (400), no route (404), wrong
        # method (405). Maps the status to a stable code so clients can
        # still dispatch on it.
        code = _FRAMEWORK_ERROR_CODES.get(exc.status_code, f"request.status_{exc.status_code}")
        title_by_code = {
            "request.body_invalid": "Malformed request body",
            "request.not_found": "Route not found",
            "request.method_not_allowed": "Method not allowed",
            "request.unsupported_media_type": "Unsupported media type",
        }
        return _render(
            request,
            TulipProblem(
                code=code,
                title=title_by_code.get(code, "Request error"),
                status=exc.status_code,
                detail=str(exc.detail) if exc.detail else None,
                headers=dict(exc.headers) if exc.headers else None,
            ),
        )
