"""ImportBatch + StatementLine API schemas (P5.2.a)."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class StatementLineRead(BaseModel):
    """One parsed bank-statement row, as surfaced by the API."""

    id: UUID
    line_number: int
    posted_date: date_type
    amount: Decimal
    currency: str
    description: str
    counterparty: str | None
    reference: str | None
    fitid: str | None
    is_excluded: bool
    reconciliation_match_id: UUID | None


class ImportBatchSummary(BaseModel):
    """Response for ``POST /v1/imports`` — header + per-bucket counts."""

    id: UUID
    account_id: UUID
    source_format: str
    source_filename: str
    status: str
    statement_line_count: int = Field(
        description=(
            "Total number of parsed statement lines persisted in this batch. "
            "Equals ``imported_count + skipped_count + error_count`` for a "
            "fresh upload; equals the persisted-line row count thereafter."
        )
    )
    imported_count: int
    skipped_count: int
    error_count: int
    created_at: datetime


class ImportBatchRead(BaseModel):
    """Response for ``GET /v1/imports/{id}`` — summary + full line list."""

    id: UUID
    account_id: UUID
    source_format: str
    source_filename: str
    status: str
    imported_count: int
    skipped_count: int
    error_count: int
    created_at: datetime
    applied_at: datetime | None
    reverted_at: datetime | None
    lines: list[StatementLineRead]
