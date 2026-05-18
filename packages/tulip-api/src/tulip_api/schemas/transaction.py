"""Transaction API schemas."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PostingCreate(BaseModel):
    """One posting in a transaction-create request."""

    account_id: UUID
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    memo: str | None = Field(default=None, max_length=500)
    pool_id: UUID | None = Field(
        default=None,
        description=(
            "Optional allocation pool (envelope or sinking fund). When set, "
            "the server auto-pairs a shadow-ledger transaction; see ADR-0001."
        ),
    )


class TransactionCreate(BaseModel):
    """Body for POST /v1/transactions."""

    date: date_type
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(
        default=None,
        description=(
            "Optional free-text transaction-level annotation. Stored "
            "encrypted at rest; round-trips on GET. Distinct from the "
            "headline ``description`` (which is required and short) and "
            "from posting-level ``memo`` (which belongs on the leg)."
        ),
    )
    postings: list[PostingCreate] = Field(min_length=2)


class PostingRead(BaseModel):
    """Posting in a response."""

    id: UUID
    account_id: UUID
    amount: Decimal
    currency: str
    memo: str | None
    pool_id: UUID | None = None


class TransactionRead(BaseModel):
    """Response for a single transaction."""

    id: UUID
    date: date_type
    description: str
    reference: str | None
    notes: str | None = None
    status: str
    postings: list[PostingRead]
    paired_shadow_tx_id: UUID | None = None
    voided_by_transaction_id: UUID | None = None
    voided_at: datetime | None = None


class TransactionVoidRequest(BaseModel):
    """Body for POST /v1/transactions/{id}/void (P5.0)."""

    reason: str = Field(min_length=1, max_length=500)
    reversal_date: date_type | None = Field(
        default=None,
        description=(
            "Date for the reversal sibling. Defaults to today. The reversal "
            "date — not the source's date — is checked against open periods."
        ),
    )


class TransactionVoidResponse(BaseModel):
    """Response for POST /v1/transactions/{id}/void (P5.0)."""

    source_id: UUID
    reversal_id: UUID
    voided_at: datetime
    paired_shadow_tx_id_voided: UUID | None = Field(
        default=None,
        description=(
            "If the source had a paired shadow tx (per ADR-0001), it has "
            "been auto-voided in the same atomic commit. Null otherwise."
        ),
    )


class TransactionReplaceRequest(BaseModel):
    """Body for POST /v1/transactions/{id}/replace (#209a).

    The atomic void-and-recreate primitive: in a single commit the source
    is voided (sibling reversal created, status flip) and a brand-new
    transaction is posted with the edited shape. The ``reason`` field
    flows into the reversal's description so the audit trail records
    *why* the source was voided. ``reversal_date`` defaults to today and
    is checked against open periods; the replacement's ``date`` is
    checked separately.
    """

    date: date_type
    description: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(
        default=None,
        description=(
            "Optional free-text transaction-level annotation on the "
            "replacement. Stored encrypted at rest."
        ),
    )
    postings: list[PostingCreate] = Field(min_length=2)
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Free-text reason for the edit; flows into the reversal's description.",
    )
    reversal_date: date_type | None = Field(
        default=None,
        description=(
            "Date for the reversal sibling. Defaults to today. The reversal "
            "date — not the source's date — is checked against open periods."
        ),
    )


class TransactionReplaceResponse(BaseModel):
    """Response for POST /v1/transactions/{id}/replace (#209a)."""

    source_id: UUID
    reversal_id: UUID
    replacement_id: UUID
    voided_at: datetime
    paired_shadow_tx_id_voided: UUID | None = Field(
        default=None,
        description=(
            "If the source had a paired shadow tx (per ADR-0001), it has "
            "been auto-voided in the same atomic commit. Null otherwise."
        ),
    )


class TransactionRectifyRequest(BaseModel):
    """PATCH /v1/transactions/{id}/description body — GDPR Art. 16 rectification (#242).

    Mutates ``description`` / ``reference`` / ``notes`` on a POSTED or
    RECONCILED transaction in place. Postings, status, and date are out of
    scope — those still require void-and-recreate. ``notes`` follows
    PATCH semantics: omitting the key leaves the column unchanged; sending
    ``null`` clears it. ``description`` is non-nullable on the underlying
    row, so it cannot be set to ``null``. At least one of the three keys
    must be present.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(
        default=None,
        description=(
            "Free-text transaction-level annotation. Omit to leave "
            "unchanged; send ``null`` to clear."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not (self.model_fields_set & {"description", "reference", "notes"}):
            raise ValueError(
                "At least one of 'description', 'reference', or 'notes' must be provided."
            )
        if "description" in self.model_fields_set and self.description is None:
            raise ValueError("'description' cannot be null; omit the key to leave it unchanged.")
        return self


class TransactionUpdate(BaseModel):
    """PATCH /v1/transactions/{id} body — PENDING-only, all fields optional.

    For ``notes`` specifically, omitting the key keeps the current value;
    sending ``null`` clears the encrypted-notes column. The router
    distinguishes the two via Pydantic's ``model_fields_set``.
    """

    date: date_type | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(
        default=None,
        description=(
            "Free-text transaction-level annotation. Omit to leave "
            "unchanged; send ``null`` to clear."
        ),
    )
    postings: list[PostingCreate] | None = Field(default=None, min_length=2)
