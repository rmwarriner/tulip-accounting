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
            "Sum of POSTED + RECONCILED postings on this account in its currency. "
            "Pending transactions are not included."
        ),
    )
    as_of: date


class TrialBalanceRow(BaseModel):
    """One row of the trial-balance report (per account, per currency)."""

    account_id: UUID
    code: str | None = None
    name: str
    type: str
    currency: str
    balance: Decimal


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
