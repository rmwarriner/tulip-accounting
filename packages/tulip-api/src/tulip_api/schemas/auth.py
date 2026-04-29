"""Auth request/response schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Body for POST /v1/auth/register."""

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

    refresh_token: str


class LogoutRequest(BaseModel):
    """Body for POST /v1/auth/logout."""

    refresh_token: str
