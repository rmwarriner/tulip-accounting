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
    SuggestBudgetRequest,
    SuggestBudgetResponse,
)
from tulip_api.services.proposal_executor import (
    execute_approved_proposal,
    supported_proposal_kinds,
)
from tulip_storage.models import PendingProposal, ProposalCreatorKind, ProposalStatus
from tulip_storage.repositories import AuditLogWriter, PendingProposalRepository

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


class ProposalNotDeletableError(TulipProblem):
    """A delete was attempted on a proposal that is not REJECTED."""

    def __init__(self, current_status: str) -> None:
        """Build the proposal.not_deletable problem (#240)."""
        super().__init__(
            code="proposal.not_deletable",
            title="Proposal not deletable",
            status=409,
            detail=(
                f"Proposal is in status {current_status!r}; only rejected "
                "proposals can be hard-deleted. Approved proposals stay for "
                "audit-chain integrity; pending proposals must be rejected first."
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


def _ai_invocation_id_str(proposal: PendingProposal) -> str | None:
    """Stringify a proposal's ai_invocation_id for audit-row metadata (#240).

    Carried on every ``proposal.*`` audit row so the chain back to the
    originating ``ai_invocations`` row is queryable; ``None`` for
    user-originated proposals.
    """
    return str(proposal.ai_invocation_id) if proposal.ai_invocation_id else None


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
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ProposalRead:
    """Create a new user-originated pending proposal.

    Always stamps ``created_by_kind=user``. AI-originated proposals are
    written via ``PendingProposalRepository.create`` from inside the
    capability layer (e.g. ``/v1/ai/proposals/suggest/budget``) — that
    path stamps ``ai_agent`` server-side with a verified ``ai_invocation_id``.
    See #218 for why we don't accept the field on this HTTP body.
    """
    repo = PendingProposalRepository(session, claims.household_id)
    row = repo.create(
        kind=body.kind,
        title=body.title,
        payload=body.payload,
        rationale=body.rationale,
        created_by_kind=ProposalCreatorKind.USER.value,
        created_by_user_id=claims.user_id,
        ai_invocation_id=None,
    )
    AuditLogWriter(session, claims.household_id).write(
        action="proposal.create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="proposal",
        entity_id=row.id,
        after={"kind": row.kind, "title": row.title},
        request_id=_request_uuid(request),
        metadata={"ai_invocation_id": _ai_invocation_id_str(row)},
    )
    session.commit()
    log.info(
        "proposal.created",
        proposal_id=str(row.id),
        kind=row.kind,
        created_by_kind=ProposalCreatorKind.USER.value,
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
    AuditLogWriter(session, claims.household_id).write(
        action="proposal.approve",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="proposal",
        entity_id=proposal_id,
        before={"status": ProposalStatus.PENDING.value},
        after={"status": ProposalStatus.APPROVED.value, "decision_note": note},
        request_id=_request_uuid(request),
        metadata={"ai_invocation_id": _ai_invocation_id_str(proposal)},
    )
    session.commit()
    log.info("proposal.approved", proposal_id=str(proposal_id), kind=proposal.kind)
    return _to_read(updated)


@router.post(
    "/{proposal_id}/reject",
    response_model=ProposalRead,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("proposal.not_found"),
        409: problem_response("proposal.already_decided"),
        422: problem_response("validation.failed"),
    },
)
def reject_proposal(
    proposal_id: UUID,
    request: Request,
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
    before_status = proposal.status
    updated = repo.mark_decided(
        proposal_id,
        status=ProposalStatus.REJECTED.value,
        decided_by_user_id=claims.user_id,
        note=note,
    )
    assert updated is not None  # noqa: S101
    AuditLogWriter(session, claims.household_id).write(
        action="proposal.reject",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="proposal",
        entity_id=proposal_id,
        before={"status": before_status},
        after={"status": ProposalStatus.REJECTED.value, "decision_note": note},
        request_id=_request_uuid(request),
        metadata={"ai_invocation_id": _ai_invocation_id_str(proposal)},
    )
    session.commit()
    log.info("proposal.rejected", proposal_id=str(proposal_id), kind=proposal.kind)
    return _to_read(updated)


@router.delete(
    "/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("proposal.not_found"),
        409: problem_response("proposal.not_deletable"),
    },
)
def delete_proposal(
    proposal_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Hard-delete a REJECTED proposal (admin-only, #240).

    Only rejected proposals can be removed — an AI hallucination baked
    into a rejected proposal's payload / rationale / title should be
    erasable. Approved proposals stay (audit-chain integrity); pending
    proposals must be rejected first. The deletion itself is audited.
    """
    repo = PendingProposalRepository(session, claims.household_id)
    proposal = repo.get(proposal_id)
    if proposal is None:
        raise ProposalNotFoundError()
    if proposal.status != ProposalStatus.REJECTED.value:
        raise ProposalNotDeletableError(proposal.status)

    before = {"status": proposal.status, "kind": proposal.kind, "title": proposal.title}
    ai_invocation_id = _ai_invocation_id_str(proposal)
    repo.delete(proposal_id)
    AuditLogWriter(session, claims.household_id).write(
        action="proposal.delete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="proposal",
        entity_id=proposal_id,
        before=before,
        after=None,
        request_id=_request_uuid(request),
        metadata={"ai_invocation_id": ai_invocation_id},
    )
    session.commit()
    log.info("proposal.deleted", proposal_id=str(proposal_id), kind=before["kind"])


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


@router.post(
    "/suggest/budget",
    response_model=SuggestBudgetResponse,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("envelope.not_found"),
        422: problem_response("validation.failed"),
    },
)
async def suggest_envelope_budget(
    body: SuggestBudgetRequest,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> SuggestBudgetResponse:
    """AI-suggest a new ``budget_amount`` for one envelope (P6.4.b).

    Pulls the envelope's 60-day spending series, calls
    ``AIProposalCapability.suggest_envelope_budget``, and writes the
    returned proposal to the queue with
    ``created_by_kind=ai_agent`` + the capability's audit row linked via
    ``ai_invocation_id``. The user then approves / rejects via the
    standard inbox flow.

    Failures (capability error, envelope missing) leave no proposal but
    do leave the ``ai_invocations`` audit row the capability wrote.
    """
    from datetime import UTC, datetime, timedelta
    from datetime import date as date_type
    from decimal import Decimal

    from sqlalchemy.orm import sessionmaker as _sessionmaker

    from tulip_ai import AIProposalCapability, LitellmAdapter
    from tulip_api.config import get_settings
    from tulip_storage.models import Household, User
    from tulip_storage.repositories import EnvelopeRepository, ShadowTransactionRepository

    found = EnvelopeRepository(session, claims.household_id).get(body.envelope_id)
    if found is None:
        from tulip_api.errors import EnvelopeNotFoundError

        raise EnvelopeNotFoundError()
    pool, envelope = found

    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=59)
    spend_map = ShadowTransactionRepository(
        session, claims.household_id
    ).daily_spend_series_for_pool(pool.id, currency=pool.currency, from_date=cutoff, to_date=today)
    series: list[tuple[date_type, Decimal]] = [
        (
            cutoff + timedelta(days=i),
            spend_map.get(cutoff + timedelta(days=i), Decimal("0")),
        )
        for i in range(60)
    ]

    settings = get_settings()
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    user = session.get(User, (claims.household_id, claims.user_id))
    api_key: str | None = None
    provider = household.ai_policy.get("default_provider")
    if isinstance(provider, str):
        from tulip_api.routers.ai import _resolve_provider_key

        api_key = _resolve_provider_key(
            household=household,
            user=user,
            provider=provider,
            master_key=settings.master_key,
        )

    bind = session.get_bind()
    cap_session_maker = _sessionmaker(bind, expire_on_commit=False)
    capability = AIProposalCapability(session_maker=cap_session_maker, adapter=LitellmAdapter())
    result = await capability.suggest_envelope_budget(
        household_id=claims.household_id,
        actor_user_id=claims.user_id,
        api_key=api_key,
        envelope_id=pool.id,
        envelope_name=pool.name,
        currency=pool.currency,
        current_budget=envelope.budget_amount,
        recent_spend_series=series,
    )
    if result.proposal is None:
        return SuggestBudgetResponse(proposal=None, error=result.error)

    repo = PendingProposalRepository(session, claims.household_id)
    row = repo.create(
        kind=result.proposal.kind,
        title=result.proposal.title,
        payload=result.proposal.payload,
        rationale=result.proposal.rationale,
        created_by_kind=ProposalCreatorKind.AI_AGENT.value,
        created_by_user_id=claims.user_id,
        ai_invocation_id=result.proposal.ai_invocation_id,
    )
    session.commit()
    log.info(
        "proposal.suggested",
        proposal_id=str(row.id),
        kind=row.kind,
        envelope_id=str(pool.id),
    )
    return SuggestBudgetResponse(proposal=_to_read(row), error=None)
