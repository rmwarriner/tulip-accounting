"""Transaction API schemas."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PostingCreate(BaseModel):
    """One posting in a transaction-create request."""

    account_id: UUID
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    memo: str | None = Field(default=None, max_length=500)


class TransactionCreate(BaseModel):
    """Body for POST /v1/transactions."""

    date: date_type
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    postings: list[PostingCreate] = Field(min_length=2)


class PostingRead(BaseModel):
    """Posting in a response."""

    id: UUID
    account_id: UUID
    amount: Decimal
    currency: str
    memo: str | None


class TransactionRead(BaseModel):
    """Response for a single transaction."""

    id: UUID
    date: date_type
    description: str
    reference: str | None
    status: str
    postings: list[PostingRead]
