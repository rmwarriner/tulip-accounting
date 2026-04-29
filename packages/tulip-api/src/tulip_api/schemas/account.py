"""Account API schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    """Body for POST /v1/accounts."""

    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern=r"^(asset|liability|equity|income|expense)$")
    currency: str = Field(min_length=3, max_length=3)
    code: str | None = Field(default=None, max_length=50)
    subtype: str | None = Field(default=None, max_length=50)
    parent_account_id: UUID | None = None
    visibility: str = Field(default="shared", pattern=r"^(shared|private)$")


class AccountUpdate(BaseModel):
    """Body for PATCH /v1/accounts/{id}. Each field is optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    subtype: str | None = Field(default=None, max_length=50)
    visibility: str | None = Field(default=None, pattern=r"^(shared|private)$")


class AccountRead(BaseModel):
    """Response shape for GET /v1/accounts and friends."""

    id: UUID
    code: str | None
    name: str
    type: str
    subtype: str | None
    currency: str
    visibility: str
    is_active: bool
    parent_account_id: UUID | None
