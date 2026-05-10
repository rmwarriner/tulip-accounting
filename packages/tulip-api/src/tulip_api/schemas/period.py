"""Schemas for ``/v1/periods`` (#136 — ``tulip periods`` CLI surface)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PeriodRead(BaseModel):
    """One row in the periods response."""

    id: UUID
    start_date: date
    end_date: date
    status: Literal["open", "soft_closed"] = Field(
        description="Soft-close is the v1 model; hard-close is deferred (ARCHITECTURE §3)."
    )
    closed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp the period was last soft-closed; null while open.",
    )
    closed_by_user_id: UUID | None = Field(
        default=None,
        description="The user who issued the soft-close, or null while open.",
    )
