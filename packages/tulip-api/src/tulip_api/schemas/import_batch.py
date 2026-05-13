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


class ImportBatchListItem(BaseModel):
    """One row of ``GET /v1/imports`` — batch summary without per-line detail.

    Mirrors :class:`ImportBatchRead` but omits the embedded statement-line
    list (which can be hundreds of rows) and the ``applied_at`` /
    ``reverted_at`` timestamps — discovery surface is "what's there", not
    "what state-machine transitions has it gone through".
    """

    id: UUID
    account_id: UUID
    source_format: str
    source_filename: str
    status: str
    imported_count: int
    skipped_count: int
    created_at: datetime


class ImportBatchListResponse(BaseModel):
    """Response for ``GET /v1/imports`` — page of batches + opaque cursor.

    ``next_cursor`` is non-null exactly when more rows are available; the
    client passes it back as ``?after=<cursor>`` to fetch the next page.
    The cursor encoding is an opaque base64 token — clients must treat it
    as a black box.
    """

    items: list[ImportBatchListItem]
    next_cursor: str | None = None


class ImportBatchApplyResponse(BaseModel):
    """Response for ``POST /v1/imports/{id}/apply`` (P5.4.a)."""

    batch_id: UUID
    status: str = Field(description="The new ``status`` of the batch (always ``applied``).")
    created_count: int = Field(
        description="Number of PENDING ledger transactions created from the batch's lines."
    )
    skipped_count: int = Field(
        description=(
            "Number of statement lines that were not promoted because they were "
            "already excluded or already promoted from a prior partial promote."
        )
    )
    transaction_ids: list[UUID] = Field(
        description="The IDs of the newly-created PENDING transactions, in line order."
    )


class StatementLinePromoteResponse(BaseModel):
    """Response for ``POST /v1/imports/{batch_id}/lines/{line_id}/promote`` (P5.4.a)."""

    statement_line_id: UUID
    transaction_id: UUID
