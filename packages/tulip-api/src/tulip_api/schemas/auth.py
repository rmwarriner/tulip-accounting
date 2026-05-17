"""Auth request/response schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Security audit L-13 (#350): every request schema in this file uses
# ``extra="forbid"`` so undeclared fields surface as 422 instead of being
# silently ignored. Response schemas can keep the default since clients
# never construct them; mass-assignment is the input-side concern.


class RegisterRequest(BaseModel):
    """Body for POST /v1/auth/register."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    household_name: str = Field(min_length=1, max_length=200)


class RegisterResponse(BaseModel):
    """Response from successful registration."""

    user_id: UUID
    household_id: UUID
    role: str


class LoginRequest(BaseModel):
    """Body for POST /v1/auth/login."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Issued token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 — RFC-6750 token-type literal, not a credential
    expires_in: int  # seconds until access token expires


class RefreshRequest(BaseModel):
    """Body for POST /v1/auth/refresh."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class LogoutRequest(BaseModel):
    """Body for POST /v1/auth/logout."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class MfaEnrollResponse(BaseModel):
    """Response from POST /v1/auth/mfa/enroll.

    The plaintext ``secret`` is returned exactly once — at enrollment time
    — so the user can scan or paste it into an authenticator app. After
    verification the secret is only retrievable in encrypted form from
    the database.
    """

    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    """Body for POST /v1/auth/mfa/verify."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=12)


class MfaLoginRequest(BaseModel):
    """Body for POST /v1/auth/login/mfa (the step-2 challenge response)."""

    model_config = ConfigDict(extra="forbid")

    mfa_token: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=12)


class MfaRecoveryLoginRequest(BaseModel):
    """Body for POST /v1/auth/login/recover (alternative step-2 path)."""

    model_config = ConfigDict(extra="forbid")

    mfa_token: str = Field(min_length=1)
    recovery_code: str = Field(min_length=1, max_length=64)


class MfaRegenerateRequest(BaseModel):
    """Body for POST /v1/auth/mfa/recovery-codes/regenerate.

    Requires a current TOTP code as the "MFA-fresh" gate — bare access
    tokens are not enough for this sensitive operation.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=12)


class MfaRecoveryCodesResponse(BaseModel):
    """Recovery codes returned at enrollment-verify or regeneration.

    Returned by ``/v1/auth/mfa/verify`` (slice c) and
    ``/v1/auth/mfa/recovery-codes/regenerate``. The plaintext is shown
    to the user **only** at these endpoints — once. After that they're
    recoverable only via regeneration.
    """

    recovery_codes: list[str]


class MfaRecoveryStatusResponse(BaseModel):
    """Response from GET /v1/auth/mfa/recovery-codes/status."""

    remaining: int
    total: int


class PasswordChangeRequest(BaseModel):
    """Body for POST /v1/auth/password/change (#242).

    Requires the caller's current password as proof of possession and
    a new password meeting the same length floor as register's password
    field. All outstanding refresh tokens for the user are revoked when
    the change succeeds.
    """

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=200)
