"""Reconciliation API schemas (P5.4.b).

Pydantic models for the reconciliation envelope + auto-match endpoints.
The reconciliation flow is: open envelope -> POST /auto-match runs the
matcher and persists candidate match rows -> user reviews + rejects
unwanted matches (and in P5.4.c adds manual ones) -> POST /complete
denormalises ``reconciled_at`` onto matched transactions.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ReconciliationCreate(BaseModel):
    """Request body for ``POST /v1/reconciliations``."""

    account_id: UUID
    statement_period_start: date_type
    statement_period_end: date_type
    statement_starting_balance: Decimal
    statement_ending_balance: Decimal
    currency: str = Field(min_length=3, max_length=3)
    source_import_batch_id: UUID = Field(
        description=(
            "The import batch whose statement lines this reconciliation will "
            "match against. P5.4.b requires one batch per reconciliation; "
            "multi-batch reconciliations are out of scope."
        ),
    )


class ReconciliationRead(BaseModel):
    """One reconciliation envelope."""

    id: UUID
    account_id: UUID
    statement_period_start: date_type
    statement_period_end: date_type
    statement_starting_balance: Decimal
    statement_ending_balance: Decimal
    currency: str
    status: str
    source_import_batch_id: UUID | None
    created_at: datetime
    completed_at: datetime | None


class MatchRead(BaseModel):
    """One reconciliation match row."""

    id: UUID
    reconciliation_id: UUID
    statement_line_id: UUID
    ledger_transaction_id: UUID
    match_amount: Decimal
    currency: str
    confidence: str | None = Field(
        description="HIGH/MEDIUM/LOW for matcher-produced; null for manual matches."
    )
    matcher_version: str | None
    created_by_user_id: UUID | None
    created_at: datetime


class StatementLineInbox(BaseModel):
    """A statement line not yet matched in this reconciliation."""

    id: UUID
    line_number: int
    posted_date: date_type
    amount: Decimal
    currency: str
    description: str
    counterparty: str | None
    reference: str | None
    fitid: str | None


class LedgerTransactionInbox(BaseModel):
    """A ledger transaction in the reconciliation's window not yet matched."""

    id: UUID
    date: date_type
    description: str
    reference: str | None
    status: str


class ReconciliationInboxResponse(BaseModel):
    """Response for ``GET /v1/reconciliations/{id}`` — envelope + review pane."""

    reconciliation: ReconciliationRead
    matches: list[MatchRead]
    unmatched_statement_lines: list[StatementLineInbox]
    unmatched_ledger_transactions: list[LedgerTransactionInbox]


class AutoMatchResponse(BaseModel):
    """Response for ``POST /v1/reconciliations/{id}/auto-match``."""

    reconciliation_id: UUID
    matches_created: int
    candidate_summary: dict[str, int] = Field(
        description="Per-confidence counts: {'high': N, 'medium': N, 'low': N}."
    )


class CompleteResponse(BaseModel):
    """Response for ``POST /v1/reconciliations/{id}/complete``."""

    reconciliation_id: UUID
    status: str
    completed_at: datetime
    affected_transaction_count: int


class ManualMatchCreate(BaseModel):
    """Request body for ``POST /v1/reconciliations/{id}/matches`` (manual match)."""

    statement_line_id: UUID
    ledger_transaction_id: UUID
    match_amount: Decimal
    currency: str = Field(min_length=3, max_length=3)


class CarryForwardCreate(BaseModel):
    """Request body for ``POST /v1/reconciliations/{id}/carry-forward``."""

    transaction_ids: list[UUID] = Field(min_length=1)


class CarryForwardResponse(BaseModel):
    """Response for ``POST /v1/reconciliations/{id}/carry-forward``."""

    reconciliation_id: UUID
    transaction_ids: list[UUID]


class ReconciliationListResponse(BaseModel):
    """Response for ``GET /v1/reconciliations`` (P5.4.d)."""

    items: list[ReconciliationRead]
