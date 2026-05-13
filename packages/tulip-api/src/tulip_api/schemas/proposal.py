"""Schemas for ``/v1/ai/proposals`` (P6.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProposalCreate(BaseModel):
    """Body for ``POST /v1/ai/proposals``.

    User-driven manual-propose path only — AI-originated proposals are
    written via ``PendingProposalRepository.create`` from inside the
    capability (e.g. ``/v1/ai/proposals/suggest/budget``), never through
    this HTTP body.

    ``ai_invocation_id`` is deliberately not accepted here: it's a
    server-side link from a proposal to the audit row that produced it,
    and accepting it from the client lets a user spoof
    ``created_by_kind=ai_agent`` on a proposal of their own making (#218).
    ``extra="forbid"`` rejects unknown fields loudly so older clients
    that send ``ai_invocation_id`` get a 422 rather than silent drop.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        min_length=1,
        max_length=40,
        description="Proposal kind; must match a registered executor.",
    )
    title: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]
    rationale: str = ""


class ProposalRead(BaseModel):
    """One proposal row."""

    id: UUID
    created_at: datetime
    kind: str
    title: str
    rationale: str
    payload: dict[str, Any]
    status: str
    created_by_kind: str
    created_by_user_id: UUID | None
    ai_invocation_id: UUID | None
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decision_note: str | None


class ProposalDecisionBody(BaseModel):
    """Optional body for approve / reject — just a free-text note."""

    note: str | None = None


class SuggestBudgetRequest(BaseModel):
    """Body for ``POST /v1/ai/proposals/suggest/budget`` (P6.4.b)."""

    envelope_id: UUID


class SuggestBudgetResponse(BaseModel):
    """Result of an AI budget suggestion run.

    On success ``proposal`` carries the newly created ``PendingProposal``
    row; ``error`` is populated only when the capability could not
    produce a suggestion (no key, disabled policy, malformed model
    response). The ai_invocations audit row exists either way.
    """

    proposal: ProposalRead | None
    error: str | None = None
