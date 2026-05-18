"""Account API schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


class AccountCreate(BaseModel):
    """Body for POST /v1/accounts."""

    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern=r"^(asset|liability|equity|income|expense)$")
    currency: str = Field(min_length=3, max_length=3)
    code: str | None = Field(default=None, max_length=200)
    subtype: str | None = Field(default=None, max_length=50)
    parent_account_id: UUID | None = None
    visibility: str = Field(default="shared", pattern=r"^(shared|private)$")
    create_parents: bool = Field(
        default=False,
        description=(
            "When true, ``code`` is parsed as a colon-delimited path "
            "(``assets:current:checking``) and every segment that doesn't "
            "already exist is auto-created in the same commit. The root "
            "segment maps to the account type via the same ``_TYPE_ALIASES`` "
            "table the resolver uses for hierarchical-path lookups (#46)."
        ),
    )


class AccountUpdate(BaseModel):
    """Body for PATCH /v1/accounts/{id}. Each field is optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    subtype: str | None = Field(default=None, max_length=50)
    visibility: str | None = Field(default=None, pattern=r"^(shared|private)$")
    parent_account_id: UUID | None = Field(
        default=None,
        description=(
            "Reparent under another account. Subject to the same type / "
            "currency / visibility / no-cycle rules as POST /v1/accounts. "
            "Currently no way to clear the parent via PATCH; create a new "
            "top-level account instead."
        ),
    )


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
    parents_created: list[AccountRead] | None = Field(
        default=None,
        description=(
            "Populated only on responses to POST /v1/accounts with "
            "create_parents=true: the auto-created ancestors (root → "
            "leaf-parent) in creation order. Null on GET / PATCH and on "
            "POST without create_parents (#46)."
        ),
    )
