"""ReconciliationMatchRepository — CRUD for the M:N match table (P5.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from tulip_storage.models import (
    MatchConfidence,
    ReconciliationMatch,
    StatementLine,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ReconciliationMatchRepository:
    """Persists matches and queries them within one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, match_id: UUID) -> ReconciliationMatch | None:
        """Return the ReconciliationMatch by id, or None."""
        return self._session.execute(
            select(ReconciliationMatch).where(
                ReconciliationMatch.household_id == self._household_id,
                ReconciliationMatch.id == match_id,
            )
        ).scalar_one_or_none()

    def list_for_reconciliation(self, reconciliation_id: UUID) -> list[ReconciliationMatch]:
        """Return all matches for a reconciliation."""
        return list(
            self._session.execute(
                select(ReconciliationMatch).where(
                    ReconciliationMatch.household_id == self._household_id,
                    ReconciliationMatch.reconciliation_id == reconciliation_id,
                )
            )
            .scalars()
            .all()
        )

    def list_for_transaction(self, ledger_transaction_id: UUID) -> list[ReconciliationMatch]:
        """Return all matches against a ledger transaction (any reconciliation)."""
        return list(
            self._session.execute(
                select(ReconciliationMatch).where(
                    ReconciliationMatch.household_id == self._household_id,
                    ReconciliationMatch.ledger_transaction_id == ledger_transaction_id,
                )
            )
            .scalars()
            .all()
        )

    def create(
        self,
        *,
        reconciliation_id: UUID,
        statement_line_id: UUID | None,
        ledger_transaction_id: UUID,
        match_amount: Decimal,
        currency: str,
        confidence: MatchConfidence | None = None,
        matcher_version: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> ReconciliationMatch:
        """Insert a new match row.

        Matcher-produced matches set ``confidence`` + ``matcher_version`` and
        leave ``created_by_user_id`` NULL. Manual matches do the inverse.
        Paper-statement matches (#275) leave ``statement_line_id`` NULL
        because there's no imported batch to point at.
        """
        match = ReconciliationMatch(
            household_id=self._household_id,
            id=uuid4(),
            reconciliation_id=reconciliation_id,
            statement_line_id=statement_line_id,
            ledger_transaction_id=ledger_transaction_id,
            match_amount=match_amount,
            currency=currency,
            confidence=confidence,
            matcher_version=matcher_version,
            created_by_user_id=created_by_user_id,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(match)
        self._session.flush()
        # Update the statement line's denormalised pointer (only when a line
        # is supplied — paper-statement matches don't have one).
        if statement_line_id is not None:
            line = self._session.get(StatementLine, (self._household_id, statement_line_id))
            if line is not None:
                line.reconciliation_match_id = match.id
                self._session.flush()
        return match

    def filter_to_completed_recons(self, match_ids: set[UUID]) -> set[UUID]:
        """Return the subset of ``match_ids`` whose reconciliation is COMPLETE.

        Used by the inbox endpoint to filter out statement lines whose
        ``reconciliation_match_id`` points at a match in a prior completed
        reconciliation (issue #127). The matches themselves still exist
        (cascade-deleting on revert handles abandonment); we filter on the
        parent reconciliation's status to know "this line is already
        accounted for elsewhere."
        """
        if not match_ids:
            return set()
        from tulip_storage.models import Reconciliation, ReconciliationStatus

        rows = self._session.execute(
            select(ReconciliationMatch.id)
            .join(
                Reconciliation,
                (Reconciliation.id == ReconciliationMatch.reconciliation_id)
                & (Reconciliation.household_id == ReconciliationMatch.household_id),
            )
            .where(
                ReconciliationMatch.household_id == self._household_id,
                ReconciliationMatch.id.in_(match_ids),
                Reconciliation.status == ReconciliationStatus.COMPLETE,
            )
        ).all()
        return {row[0] for row in rows}

    def reject(self, match_id: UUID) -> None:
        """Delete a match row (rejection per ADR-0004 §Q4)."""
        match = self.get(match_id)
        if match is None:
            raise LookupError(
                f"reconciliation_match {match_id} not found in household {self._household_id}"
            )
        # Clear the statement_line's match pointer (no-op for paper-statement
        # matches where statement_line_id is NULL).
        if match.statement_line_id is not None:
            line = self._session.get(StatementLine, (self._household_id, match.statement_line_id))
            if line is not None:
                line.reconciliation_match_id = None
        self._session.execute(
            delete(ReconciliationMatch).where(
                ReconciliationMatch.household_id == self._household_id,
                ReconciliationMatch.id == match_id,
            )
        )
