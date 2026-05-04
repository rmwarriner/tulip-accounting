"""ReconciliationRepository — chokepoint for reconciliation state.

Per ADR-0004 §Q7: only ``ReconciliationRepository.complete()`` is allowed
to set ``transactions.reconciled_at`` and ``transactions.reconciliation_id``.
The architecture test in tulip-storage enforces this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, update

from tulip_storage.models import (
    Reconciliation,
    ReconciliationMatch,
    ReconciliationStatus,
    Transaction,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ReconciliationRepository:
    """Persists reconciliation events and finalises matches in this household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, reconciliation_id: UUID) -> Reconciliation | None:
        """Return the Reconciliation by id, or None."""
        return self._session.execute(
            select(Reconciliation).where(
                Reconciliation.household_id == self._household_id,
                Reconciliation.id == reconciliation_id,
            )
        ).scalar_one_or_none()

    def list_for_account(self, account_id: UUID) -> list[Reconciliation]:
        """Return reconciliations for an account, newest period first."""
        return list(
            self._session.execute(
                select(Reconciliation)
                .where(
                    Reconciliation.household_id == self._household_id,
                    Reconciliation.account_id == account_id,
                )
                .order_by(Reconciliation.statement_period_end.desc())
            )
            .scalars()
            .all()
        )

    def create(
        self,
        *,
        account_id: UUID,
        statement_period_start: date_type,
        statement_period_end: date_type,
        statement_starting_balance: Decimal,
        statement_ending_balance: Decimal,
        currency: str,
        source_import_batch_id: UUID | None = None,
        created_by_user_id: UUID | None = None,
    ) -> Reconciliation:
        """Open a new reconciliation in IN_PROGRESS status."""
        if statement_period_start > statement_period_end:
            raise ValueError(
                f"statement_period_start ({statement_period_start}) must be "
                f"<= statement_period_end ({statement_period_end})"
            )
        recon = Reconciliation(
            household_id=self._household_id,
            id=uuid4(),
            account_id=account_id,
            statement_period_start=statement_period_start,
            statement_period_end=statement_period_end,
            statement_starting_balance=statement_starting_balance,
            statement_ending_balance=statement_ending_balance,
            currency=currency,
            status=ReconciliationStatus.IN_PROGRESS,
            source_import_batch_id=source_import_batch_id,
            created_by_user_id=created_by_user_id,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(recon)
        self._session.flush()
        return recon

    def complete(self, reconciliation_id: UUID) -> Reconciliation:
        """Finalise a reconciliation; denormalise reconciled_at onto matched txs.

        Sets ``status=complete``, ``completed_at=now``, and for every
        transaction linked through ``reconciliation_matches``, populates
        ``transactions.reconciliation_id`` + ``transactions.reconciled_at``.
        This is the **only** writer of those columns (architecture test
        enforces this).
        """
        recon = self.get(reconciliation_id)
        if recon is None:
            raise LookupError(
                f"reconciliation {reconciliation_id} not found in household {self._household_id}"
            )
        completed_at = datetime.now(tz=UTC)

        matched_tx_ids = list(
            self._session.execute(
                select(ReconciliationMatch.ledger_transaction_id).where(
                    ReconciliationMatch.household_id == self._household_id,
                    ReconciliationMatch.reconciliation_id == reconciliation_id,
                )
            )
            .scalars()
            .all()
        )

        if matched_tx_ids:
            self._session.execute(
                update(Transaction)
                .where(
                    Transaction.household_id == self._household_id,
                    Transaction.id.in_(matched_tx_ids),
                )
                .values(
                    reconciliation_id=reconciliation_id,
                    reconciled_at=completed_at,
                )
            )

        recon.status = ReconciliationStatus.COMPLETE
        recon.completed_at = completed_at
        self._session.flush()
        return recon

    def abandon(self, reconciliation_id: UUID) -> Reconciliation:
        """Mark a reconciliation as abandoned without writing tx denorms."""
        recon = self.get(reconciliation_id)
        if recon is None:
            raise LookupError(
                f"reconciliation {reconciliation_id} not found in household {self._household_id}"
            )
        recon.status = ReconciliationStatus.ABANDONED
        self._session.flush()
        return recon
