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
        statement_line_id: UUID,
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
        # Update the statement line's denormalised pointer.
        line = self._session.get(StatementLine, (self._household_id, statement_line_id))
        if line is not None:
            line.reconciliation_match_id = match.id
            self._session.flush()
        return match

    def reject(self, match_id: UUID) -> None:
        """Delete a match row (rejection per ADR-0004 §Q4)."""
        match = self.get(match_id)
        if match is None:
            raise LookupError(
                f"reconciliation_match {match_id} not found in household {self._household_id}"
            )
        # Clear the statement_line's match pointer.
        line = self._session.get(StatementLine, (self._household_id, match.statement_line_id))
        if line is not None:
            line.reconciliation_match_id = None
        self._session.execute(
            delete(ReconciliationMatch).where(
                ReconciliationMatch.household_id == self._household_id,
                ReconciliationMatch.id == match_id,
            )
        )
