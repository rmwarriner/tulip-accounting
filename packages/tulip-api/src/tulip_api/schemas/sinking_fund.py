"""Sinking-fund API schemas. Mirror of envelope schemas with goal-bounded fields."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class SinkingFundCreate(BaseModel):
    """Body for ``POST /v1/sinking-funds``."""

    name: str = Field(min_length=1, max_length=200)
    currency: str = Field(min_length=3, max_length=3)
    target_amount: Decimal = Field(gt=0)
    target_date: date_type
    contribution_strategy: str = Field(
        pattern=r"^(manual|even_split|percentage_of_income)$",
    )
    contribution_amount: Decimal | None = Field(default=None, ge=0)
    visibility: str = Field(default="shared", pattern=r"^(shared|private)$")


class SinkingFundUpdate(BaseModel):
    """Body for ``PATCH /v1/sinking-funds/{id}``. Each field is optional.

    Currency is immutable. ``is_active`` is managed via DELETE.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    visibility: str | None = Field(default=None, pattern=r"^(shared|private)$")
    target_amount: Decimal | None = Field(default=None, gt=0)
    target_date: date_type | None = None
    contribution_strategy: str | None = Field(
        default=None,
        pattern=r"^(manual|even_split|percentage_of_income)$",
    )
    contribution_amount: Decimal | None = Field(default=None, ge=0)


class SinkingFundRead(BaseModel):
    """Response shape for ``GET /v1/sinking-funds`` and friends."""

    id: UUID
    name: str
    currency: str
    visibility: str
    is_active: bool
    target_amount: Decimal
    target_date: date_type
    contribution_strategy: str
    contribution_amount: Decimal | None
