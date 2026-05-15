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

from sqlalchemy import delete, select, update

from tulip_storage.models import (
    Reconciliation,
    ReconciliationMatch,
    ReconciliationStatus,
    StatementLine,
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

    def list_for_household(
        self,
        *,
        account_id: UUID | None = None,
        status: ReconciliationStatus | None = None,
    ) -> list[Reconciliation]:
        """Return reconciliations for the household, newest period first.

        Optional filters: ``account_id`` (scope to one account),
        ``status`` (scope to one workflow state). Filters AND together;
        unset filters are not applied.
        """
        query = select(Reconciliation).where(Reconciliation.household_id == self._household_id)
        if account_id is not None:
            query = query.where(Reconciliation.account_id == account_id)
        if status is not None:
            query = query.where(Reconciliation.status == status)
        query = query.order_by(Reconciliation.statement_period_end.desc())
        return list(self._session.execute(query).scalars().all())

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

    def revert(self, reconciliation_id: UUID) -> None:
        """Un-reconcile: null tx denorms, clear line pointers, delete the row.

        Per ADR-0004 §Q7, ``DELETE /v1/reconciliations/{id}?cascade=true``
        is the user-facing un-reconcile path. This is its single
        chokepoint:

        1. Null ``transactions.reconciliation_id`` + ``reconciled_at`` for
           every transaction this reconciliation completed (so they're
           re-matchable).
        2. Null ``statement_lines.reconciliation_match_id`` for every line
           in the affected matches (the FK on ``reconciliation_matches``
           is ON DELETE CASCADE, but the line's denormalised pointer has
           no FK and would otherwise dangle).
        3. Delete the reconciliation row — cascades the matches.

        Raises:
            LookupError: ``reconciliation_id`` does not exist in this household.

        """
        recon = self.get(reconciliation_id)
        if recon is None:
            raise LookupError(
                f"reconciliation {reconciliation_id} not found in household {self._household_id}"
            )

        # Pull all match rows so we know which lines + txs to clean up.
        match_rows = list(
            self._session.execute(
                select(ReconciliationMatch).where(
                    ReconciliationMatch.household_id == self._household_id,
                    ReconciliationMatch.reconciliation_id == reconciliation_id,
                )
            )
            .scalars()
            .all()
        )
        line_ids = [m.statement_line_id for m in match_rows if m.statement_line_id is not None]

        # Clear the statement_line denormalised pointers (no FK to cascade).
        if line_ids:
            self._session.execute(
                update(StatementLine)
                .where(
                    StatementLine.household_id == self._household_id,
                    StatementLine.id.in_(line_ids),
                )
                .values(reconciliation_match_id=None)
            )

        # Null the transaction denorms — only this method (and complete())
        # touch these columns; architecture test enforces.
        self._session.execute(
            update(Transaction)
            .where(
                Transaction.household_id == self._household_id,
                Transaction.reconciliation_id == reconciliation_id,
            )
            .values(reconciliation_id=None, reconciled_at=None)
        )

        # Also null any carry-forward links pointing at this reconciliation —
        # carry-forward is "this tx was counted in reconciliation X"; when X
        # is reverted, the audit trail breaks (P5.4.c).
        self._session.execute(
            update(Transaction)
            .where(
                Transaction.household_id == self._household_id,
                Transaction.carried_forward_from_reconciliation_id == reconciliation_id,
            )
            .values(carried_forward_from_reconciliation_id=None)
        )

        # Delete the reconciliation row — cascades reconciliation_matches.
        self._session.execute(
            delete(Reconciliation).where(
                Reconciliation.household_id == self._household_id,
                Reconciliation.id == reconciliation_id,
            )
        )
        self._session.flush()

    def set_carry_forward(self, transaction_id: UUID, reconciliation_id: UUID) -> None:
        """Mark a transaction as carry-forward from this reconciliation.

        Single chokepoint for ``transactions.carried_forward_from_reconciliation_id``
        writes (architecture-test enforced). Per ADR-0004 §Q3: a ledger
        transaction in the period that the user wants to defer to the
        next reconciliation gets pinned here so the current reconciliation's
        balance check ignores it but the audit trail records "this tx was
        counted in reconciliation X."

        Raises:
            LookupError: ``transaction_id`` does not exist in this household.

        """
        tx = self._session.get(Transaction, (self._household_id, transaction_id))
        if tx is None:
            raise LookupError(
                f"transaction {transaction_id} not found in household {self._household_id}"
            )
        self._session.execute(
            update(Transaction)
            .where(
                Transaction.household_id == self._household_id,
                Transaction.id == transaction_id,
            )
            .values(carried_forward_from_reconciliation_id=reconciliation_id)
        )
        self._session.flush()

    def clear_carry_forward(self, transaction_id: UUID) -> None:
        """Un-mark a transaction's carry-forward link.

        Single chokepoint for nulling
        ``transactions.carried_forward_from_reconciliation_id``. Caller is
        responsible for verifying the user intent — this method does not
        check whether the transaction was actually carried forward.

        Raises:
            LookupError: ``transaction_id`` does not exist in this household.

        """
        tx = self._session.get(Transaction, (self._household_id, transaction_id))
        if tx is None:
            raise LookupError(
                f"transaction {transaction_id} not found in household {self._household_id}"
            )
        self._session.execute(
            update(Transaction)
            .where(
                Transaction.household_id == self._household_id,
                Transaction.id == transaction_id,
            )
            .values(carried_forward_from_reconciliation_id=None)
        )
        self._session.flush()
