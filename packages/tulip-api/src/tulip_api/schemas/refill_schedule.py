"""Refill-schedule API schemas (P4.3.c).

Sits on top of the runner primitive (ADR-0002). One ``scheduled_jobs``
row per envelope per ``kind="envelope_refill"`` — enforced by the unique
partial index on ``(household_id, kind, idempotency_key)`` where
``idempotency_key = str(envelope_id)``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Security audit L-13 (#350): request schemas use extra="forbid".


class RefillScheduleCreate(BaseModel):
    """Body for ``POST /v1/envelopes/{id}/refill-schedule``."""

    model_config = ConfigDict(extra="forbid")

    rrule: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "RFC 5545 RRULE string (e.g. 'FREQ=MONTHLY;BYMONTHDAY=1'). "
            "Validated server-side via python-dateutil."
        ),
    )
    start_at: datetime = Field(
        description=(
            "Anchor for the recurrence series. The first fire is at or "
            "after this time, depending on the RRULE. Subsequent fires "
            "are computed from this anchor (so COUNT/UNTIL semantics "
            "stay stable as the schedule advances)."
        ),
    )


class RefillScheduleRead(BaseModel):
    """Response for refill-schedule reads — the relevant fields of a ``scheduled_jobs`` row."""

    id: UUID
    envelope_id: UUID
    rrule: str
    dtstart: datetime
    next_run_at: datetime
    last_run_at: datetime | None
    is_active: bool


class ScheduledJobRead(BaseModel):
    """Response shape for ``GET /v1/scheduled-jobs`` — generic across all kinds."""

    id: UUID
    kind: str
    rrule: str | None
    dtstart: datetime
    next_run_at: datetime
    last_run_at: datetime | None
    is_active: bool
    idempotency_key: str | None


class RunDueResponse(BaseModel):
    """Response from ``POST /v1/scheduled-jobs/run-due``."""

    fired: int = Field(
        description=(
            "Number of jobs that fired (regardless of success or failure). "
            "Inspect /v1/scheduled-jobs and /v1/scheduled-jobs/{id}/runs "
            "for outcomes."
        ),
    )
