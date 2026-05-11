"""HTTP surface for ``/v1/ai/proposals`` (P6.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from tulip_api.auth.deps import require_role
from tulip_api.deps import get_session
from tulip_api.errors import TulipProblem, problem_response
from tulip_api.schemas.proposal import (
    ProposalCreate,
    ProposalDecisionBody,
    ProposalRead,
)
from tulip_api.services.proposal_executor import (
    execute_approved_proposal,
    supported_proposal_kinds,
)
from tulip_storage.models import PendingProposal, ProposalCreatorKind, ProposalStatus
from tulip_storage.repositories import PendingProposalRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/ai/proposals", tags=["ai"])
log = structlog.get_logger("tulip_api.proposals")


class ProposalNotFoundError(TulipProblem):
    """The proposal id doesn't belong to the caller's household."""

    def __init__(self) -> None:
        """Build the proposal.not_found problem (P6.4)."""
        super().__init__(
            code="proposal.not_found",
            title="Proposal not found",
            status=404,
            detail="No proposal with that ID exists in this household.",
        )


class ProposalAlreadyDecidedError(TulipProblem):
    """An approve/reject was attempted on an already-decided proposal."""

    def __init__(self, current_status: str) -> None:
        """Build the proposal.already_decided problem."""
        super().__init__(
            code="proposal.already_decided",
            title="Proposal already decided",
            status=409,
            detail=(
                f"Proposal is in status {current_status!r}; it cannot be "
                "decided again. Create a new proposal if a different action "
                "is needed."
            ),
        )


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


def _to_read(row: PendingProposal) -> ProposalRead:
    return ProposalRead.model_validate(row, from_attributes=True)


@router.post(
    "",
    response_model=ProposalRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def create_proposal(
    body: ProposalCreate,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ProposalRead:
    """Create a new pending proposal.

    AI-generated proposals (P6.4.b) supply ``ai_invocation_id`` and set
    ``created_by_kind=ai_agent`` server-side based on its presence.
    Direct user-created proposals omit it and stamp ``created_by_kind=user``.
    """
    creator_kind = (
        ProposalCreatorKind.AI_AGENT.value
        if body.ai_invocation_id is not None
        else ProposalCreatorKind.USER.value
    )
    repo = PendingProposalRepository(session, claims.household_id)
    row = repo.create(
        kind=body.kind,
        title=body.title,
        payload=body.payload,
        rationale=body.rationale,
        created_by_kind=creator_kind,
        created_by_user_id=claims.user_id,
        ai_invocation_id=body.ai_invocation_id,
    )
    session.commit()
    log.info(
        "proposal.created",
        proposal_id=str(row.id),
        kind=row.kind,
        created_by_kind=creator_kind,
    )
    return _to_read(row)


@router.get(
    "",
    response_model=list[ProposalRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_proposals(
    status_filter: str | None = Query(
        default="pending",
        alias="status",
        description=(
            "Filter by status: pending / approved / rejected. "
            "Pass empty string to disable the filter."
        ),
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[ProposalRead]:
    """List proposals (newest first), filtered by status (default: pending)."""
    repo = PendingProposalRepository(session, claims.household_id)
    filter_value: str | None = status_filter if status_filter else None
    return [_to_read(r) for r in repo.list_by_status(filter_value)]


@router.post(
    "/{proposal_id}/approve",
    response_model=ProposalRead,
    responses={
        400: problem_response(
            "proposal.payload_invalid",
            "proposal.unsupported_kind",
            "request.body_invalid",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("proposal.not_found", "envelope.not_found"),
        409: problem_response("proposal.already_decided"),
        422: problem_response("validation.failed"),
    },
)
async def approve_proposal(
    proposal_id: UUID,
    request: Request,
    body: ProposalDecisionBody | None = None,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ProposalRead:
    """Approve a proposal and execute its change.

    The executor for the proposal's kind runs first; success then stamps
    ``status=approved`` on the proposal. Failures (unsupported kind,
    invalid payload, envelope vanished) leave the proposal as PENDING
    so the user can fix and retry.
    """
    repo = PendingProposalRepository(session, claims.household_id)
    proposal = repo.get(proposal_id)
    if proposal is None:
        raise ProposalNotFoundError()
    if proposal.status != ProposalStatus.PENDING.value:
        raise ProposalAlreadyDecidedError(proposal.status)

    await execute_approved_proposal(
        session,
        household_id=claims.household_id,
        proposal=proposal,
        decided_by_user_id=claims.user_id,
        request_id=_request_uuid(request),
    )
    note = body.note if body is not None else None
    updated = repo.mark_decided(
        proposal_id,
        status=ProposalStatus.APPROVED.value,
        decided_by_user_id=claims.user_id,
        note=note,
    )
    assert updated is not None  # noqa: S101 — we just got it from the repo
    session.commit()
    log.info("proposal.approved", proposal_id=str(proposal_id), kind=proposal.kind)
    return _to_read(updated)


@router.post(
    "/{proposal_id}/reject",
    response_model=ProposalRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("proposal.not_found"),
        409: problem_response("proposal.already_decided"),
        422: problem_response("validation.failed"),
    },
)
def reject_proposal(
    proposal_id: UUID,
    body: ProposalDecisionBody | None = None,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ProposalRead:
    """Mark the proposal rejected. No execution."""
    repo = PendingProposalRepository(session, claims.household_id)
    proposal = repo.get(proposal_id)
    if proposal is None:
        raise ProposalNotFoundError()
    if proposal.status not in (
        ProposalStatus.PENDING.value,
        ProposalStatus.REJECTED.value,
    ):
        raise ProposalAlreadyDecidedError(proposal.status)
    note = body.note if body is not None else None
    updated = repo.mark_decided(
        proposal_id,
        status=ProposalStatus.REJECTED.value,
        decided_by_user_id=claims.user_id,
        note=note,
    )
    assert updated is not None  # noqa: S101
    session.commit()
    log.info("proposal.rejected", proposal_id=str(proposal_id), kind=proposal.kind)
    return _to_read(updated)


@router.get(
    "/kinds",
    response_model=list[str],
    responses={401: problem_response("auth.unauthorized")},
)
def list_supported_kinds(
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
) -> list[str]:
    """Return the proposal kinds the approve flow can currently execute."""
    del claims  # authenticated only
    return list(supported_proposal_kinds())
