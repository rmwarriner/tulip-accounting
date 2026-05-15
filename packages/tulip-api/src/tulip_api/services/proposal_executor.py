"""Execute one approved ``PendingProposal`` (P6.4).

Each proposal ``kind`` maps to an executor function that interprets the
payload, performs the change via the existing domain repositories, and
writes an audit_log row with ``actor_kind`` matching the proposal's
``created_by_kind`` (so AI-proposed-then-approved changes carry the
``actor_kind=ai_agent`` audit signal per ARCHITECTURE.md §6.2).

v1 ships one kind: ``envelope_budget_update``. Adding a kind means
registering one function in ``_EXECUTORS``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tulip_api.errors import EnvelopeNotFoundError, TulipProblem
from tulip_storage.models import PendingProposal, ProposalCreatorKind
from tulip_storage.repositories import AuditLogWriter, EnvelopeRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class UnsupportedProposalKindError(TulipProblem):
    """Approve was called on a proposal whose kind has no executor."""

    def __init__(self, kind: str) -> None:
        """Build the proposal.unsupported_kind problem (P6.4)."""
        super().__init__(
            code="proposal.unsupported_kind",
            title="Proposal kind not supported",
            status=400,
            detail=(
                f"No executor is registered for proposal kind {kind!r}. "
                "Reject the proposal or implement an executor."
            ),
        )


class ProposalPayloadInvalidError(TulipProblem):
    """The proposal's payload didn't conform to the executor's expectations."""

    def __init__(self, reason: str) -> None:
        """Build the proposal.payload_invalid problem."""
        super().__init__(
            code="proposal.payload_invalid",
            title="Proposal payload is invalid",
            status=400,
            detail=reason,
        )


def _actor_kind_for(proposal: PendingProposal) -> str:
    """Map the proposal's creator kind to the audit row's ``actor_kind`` value.

    AI-created → ``ai_agent`` even when a human approved (per
    ARCHITECTURE.md §6.2: "the audit log noting actor_kind=ai_agent and
    the originating proposal id"). User-created → ``user``.
    """
    if proposal.created_by_kind == ProposalCreatorKind.AI_AGENT.value:
        return "ai_agent"
    return "user"


async def _execute_envelope_budget_update(
    session: Session,
    *,
    household_id: UUID,
    proposal: PendingProposal,
    decided_by_user_id: UUID,
    request_id: UUID | None,
) -> None:
    """Update a single envelope's ``budget_amount``.

    Payload shape: ``{"envelope_id": "<uuid>", "new_budget_amount": "<decimal>"}``.
    """
    payload: dict[str, Any] = proposal.payload
    try:
        envelope_id = UUID(str(payload["envelope_id"]))
        new_amount = Decimal(str(payload["new_budget_amount"]))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ProposalPayloadInvalidError(
            f"envelope_budget_update payload must include envelope_id (UUID) "
            f"and new_budget_amount (decimal): {exc}"
        ) from exc

    repo = EnvelopeRepository(session, household_id)
    found = repo.get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    _pool, env = found
    before = env.budget_amount

    repo.update_fields(envelope_id, budget_amount=new_amount)

    AuditLogWriter(session, household_id).write(
        action="update",
        actor_kind=_actor_kind_for(proposal),
        actor_user_id=decided_by_user_id,
        entity_type="envelope",
        entity_id=envelope_id,
        before={"budget_amount": str(before) if before is not None else None},
        after={"budget_amount": str(new_amount)},
        request_id=request_id,
        metadata={
            "proposal_id": str(proposal.id),
            "proposal_kind": proposal.kind,
            "rationale": proposal.rationale or "",
            "ai_invocation_id": (
                str(proposal.ai_invocation_id) if proposal.ai_invocation_id else None
            ),
        },
    )


ExecutorCallback = Callable[..., Awaitable[None]]

_EXECUTORS: dict[str, ExecutorCallback] = {
    "envelope_budget_update": _execute_envelope_budget_update,
}


def supported_proposal_kinds() -> tuple[str, ...]:
    """Return the proposal kinds the approve flow can execute today."""
    return tuple(_EXECUTORS.keys())


async def execute_approved_proposal(
    session: Session,
    *,
    household_id: UUID,
    proposal: PendingProposal,
    decided_by_user_id: UUID,
    request_id: UUID | None,
) -> None:
    """Dispatch to the kind's executor. Raises ``UnsupportedProposalKindError`` if missing."""
    executor = _EXECUTORS.get(proposal.kind)
    if executor is None:
        raise UnsupportedProposalKindError(proposal.kind)
    await executor(
        session,
        household_id=household_id,
        proposal=proposal,
        decided_by_user_id=decided_by_user_id,
        request_id=request_id,
    )


__all__ = [
    "ProposalPayloadInvalidError",
    "UnsupportedProposalKindError",
    "execute_approved_proposal",
    "supported_proposal_kinds",
]
