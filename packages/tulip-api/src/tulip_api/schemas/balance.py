"""Read schemas for the balance endpoints (#31)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class AccountBalanceRead(BaseModel):
    """Response from ``GET /v1/accounts/{id}/balance``."""

    account_id: UUID
    code: str | None = None
    name: str
    currency: str
    balance: Decimal = Field(
        description=(
            "Sum of POSTED + RECONCILED postings on this account in its "
            "currency. When pending_included is true, PENDING transactions "
            "are folded in as well."
        ),
    )
    as_of: date
    pending_included: bool = Field(
        default=False,
        description=(
            "True when the request passed include_pending=true and the "
            "balance reflects PENDING transactions, not just the posted "
            "ledger."
        ),
    )
    pending_count: int = Field(
        default=0,
        description=(
            "Number of PENDING transactions contributing to this balance. "
            "Always 0 when pending_included is false."
        ),
    )


class TrialBalanceRow(BaseModel):
    """One row of the trial-balance report (per account, per currency)."""

    account_id: UUID
    code: str | None = None
    name: str
    type: str
    currency: str
    balance: Decimal
    has_pending: bool = Field(
        default=False,
        description=(
            "True when pending_included is set and at least one PENDING "
            "transaction contributed to this row's balance."
        ),
    )


class CurrencyTotal(BaseModel):
    """Per-currency debit/credit summary used for the zero-sum check."""

    currency: str
    debits: Decimal
    credits: Decimal


class TrialBalanceRead(BaseModel):
    """Response from ``GET /v1/reports/trial-balance``."""

    as_of: date
    rows: list[TrialBalanceRow]
    totals_by_currency: list[CurrencyTotal] = Field(
        description=(
            "Per-currency totals of positive (debit) and negative (credit) "
            "balances. They sum to zero per currency in a healthy ledger; a "
            "non-zero sum indicates a data issue."
        ),
    )
    pending_included: bool = Field(
        default=False,
        description=(
            "True when the request passed include_pending=true. The rows "
            "and totals then reflect PENDING transactions on top of the "
            "posted ledger; rows that drew a PENDING posting carry "
            "has_pending=true."
        ),
    )
    pending_count: int = Field(
        default=0,
        description=(
            "Number of PENDING transactions folded into this report. "
            "Always 0 when pending_included is false."
        ),
    )
