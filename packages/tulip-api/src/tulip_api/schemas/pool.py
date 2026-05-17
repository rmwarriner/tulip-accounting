"""Pool-level schemas — balance reads, transfer + budget-inflow request bodies.

Used by the envelopes / sinking_funds / pools routers. Per ADR-0001, these
operate on the shadow ledger; the response shapes mirror the main-ledger
balance schemas in :mod:`tulip_api.schemas.balance`.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Security audit L-13 (#350): request schemas use extra="forbid".


class PoolBalanceRead(BaseModel):
    """Response from ``GET /v1/envelopes/{id}/balance`` and ``/v1/sinking-funds/{id}/balance``.

    The shape is identical for both pool types. ``balance`` is the sum of
    POSTED shadow postings on the pool in its currency; pending and voided
    shadow transactions don't contribute. Quantized to the currency's minor
    units so the JSON representation is the natural ``"250.00"`` rather than
    storage-precision ``"250.00000000"``.
    """

    pool_id: UUID
    name: str
    currency: str
    balance: Decimal = Field(
        description=(
            "Sum of POSTED shadow postings on this pool, in its currency. "
            "Negative values are permitted and indicate over-allocation "
            "(envelopes / sinking funds) or pending inflow declarations."
        ),
    )
    as_of: date_type


class PoolBalancesRequest(BaseModel):
    """Body for ``POST /v1/pools/balances`` (#137).

    Pool ids that don't belong to the caller's household are silently
    omitted from the response — same tenant-scoping behaviour as the
    per-pool balance endpoint. Empty ``pool_ids`` returns an empty list.
    """

    model_config = ConfigDict(extra="forbid")

    pool_ids: list[UUID] = Field(
        max_length=500,
        description=(
            "Pool UUIDs to look up. Capped at 500 per request to keep the "
            "single SQL query bounded; the typical use case (one ``tulip "
            "envelopes list`` invocation) needs <50."
        ),
    )


class TransferRequest(BaseModel):
    """Body for ``POST /v1/pools/{src_pool_id}/transfer``.

    Source pool comes from the path; destination is in the body. Both must
    be user pools (envelope or sinking_fund), active, in the same household,
    and share a currency.
    """

    model_config = ConfigDict(extra="forbid")

    dest_pool_id: UUID
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive amount to move from the source pool to the destination, "
            "in the pools' shared currency. Use a separate request to move "
            "back; transfers are not signed."
        ),
    )
    date: date_type
    description: str = Field(min_length=1, max_length=500)
    memo: str | None = Field(default=None, max_length=500)


class BudgetInflowRequest(BaseModel):
    """Body for ``POST /v1/pools/budget-inflow``.

    Declares ``amount`` of new money available to budget. Posts a shadow
    transaction with reason ``budget_inflow`` (Inflow -X / Unallocated +X).
    Lazy-creates the household's ``Inflow`` and ``Unallocated`` system pools
    for the currency if they don't already exist.
    """

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    date: date_type
    description: str = Field(min_length=1, max_length=500)
    memo: str | None = Field(default=None, max_length=500)
