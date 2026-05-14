"""PendingProposalRepository — agentic-proposal queue (P6.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import PendingProposal, ProposalStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PendingProposalRepository:
    """CRUD + decide for the household's proposal queue."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        kind: str,
        title: str,
        payload: dict[str, Any],
        created_by_kind: str,
        created_by_user_id: UUID | None,
        ai_invocation_id: UUID | None = None,
        rationale: str = "",
    ) -> PendingProposal:
        """Insert one PENDING proposal."""
        row = PendingProposal(
            household_id=self._household_id,
            id=uuid4(),
            kind=kind,
            title=title,
            rationale=rationale,
            payload=payload,
            status=ProposalStatus.PENDING.value,
            created_by_kind=created_by_kind,
            created_by_user_id=created_by_user_id,
            ai_invocation_id=ai_invocation_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, proposal_id: UUID) -> PendingProposal | None:
        """Return one proposal, or ``None`` if not in this household."""
        return self._session.execute(
            select(PendingProposal).where(
                PendingProposal.household_id == self._household_id,
                PendingProposal.id == proposal_id,
            )
        ).scalar_one_or_none()

    def delete(self, proposal_id: UUID) -> bool:
        """Hard-delete one proposal. Returns ``True`` if a row was removed.

        Caller is responsible for any status precondition (the API only
        permits deleting REJECTED proposals — approved ones stay for
        audit-chain integrity, #240).
        """
        row = self.get(proposal_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_by_status(self, status: str | None = None) -> list[PendingProposal]:
        """List proposals, newest first. ``status=None`` returns everything."""
        query = (
            select(PendingProposal)
            .where(PendingProposal.household_id == self._household_id)
            .order_by(PendingProposal.created_at.desc())
        )
        if status is not None:
            query = query.where(PendingProposal.status == status)
        return list(self._session.execute(query).scalars().all())

    def mark_decided(
        self,
        proposal_id: UUID,
        *,
        status: str,
        decided_by_user_id: UUID,
        note: str | None = None,
    ) -> PendingProposal | None:
        """Stamp ``status`` + decision metadata. Returns ``None`` if not found.

        Idempotent in the sense that a second call with the same status
        is a no-op (the timestamp doesn't move). Calling with a different
        status from the prior decision raises — proposals decide once.
        """
        row = self.get(proposal_id)
        if row is None:
            return None
        if row.status == status:
            return row
        if row.status != ProposalStatus.PENDING.value:
            raise ValueError(
                f"proposal {proposal_id} already decided as {row.status!r}; "
                f"cannot transition to {status!r}"
            )
        row.status = status
        row.decided_by_user_id = decided_by_user_id
        row.decided_at = datetime.now(UTC)
        if note is not None:
            row.decision_note = note
        self._session.flush()
        return row
