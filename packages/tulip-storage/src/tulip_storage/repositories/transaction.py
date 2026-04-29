"""TransactionRepository — persists balanced domain Transactions to the ledger.

The save flow always writes the header as PENDING first, inserts every
posting, then UPDATEs the header to its final status. The DB trigger
(see migrations 0001) validates balance on the status transition. That
two-phase flow is what lets us insert an entire transaction in one
session.commit() while still having defense-in-depth against unbalanced
writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update

from tulip_storage.models import Posting, Transaction, TransactionStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_core.transactions import Transaction as DomainTransaction
    from tulip_core.transactions import TransactionStatus as DomainTxStatus


_DOMAIN_TO_STORAGE_STATUS: dict[str, TransactionStatus] = {
    "pending": TransactionStatus.PENDING,
    "posted": TransactionStatus.POSTED,
    "reconciled": TransactionStatus.RECONCILED,
}


class TransactionRepository:
    """Persists Transactions and queries existing ones, scoped to one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, tx_id: UUID) -> Transaction | None:
        """Return the Transaction header by id, or None."""
        return self._session.execute(
            select(Transaction).where(
                Transaction.household_id == self._household_id,
                Transaction.id == tx_id,
            )
        ).scalar_one_or_none()

    def list_postings(self, tx_id: UUID) -> list[Posting]:
        """Return all postings belonging to a transaction."""
        return list(
            self._session.execute(
                select(Posting).where(
                    Posting.household_id == self._household_id,
                    Posting.transaction_id == tx_id,
                )
            )
            .scalars()
            .all()
        )

    def save_balanced(self, domain_tx: DomainTransaction) -> Transaction:
        """Persist a balanced Domain Transaction.

        Inserts the header as PENDING, then all postings, then UPDATEs the
        header to the requested final status. The balance trigger validates
        on the UPDATE; if postings are unbalanced the transaction aborts.
        """
        target_status = self._domain_to_storage(domain_tx.status)
        return self._save(domain_tx, target_status)

    def _save(
        self,
        domain_tx: DomainTransaction,
        target_status: TransactionStatus,
    ) -> Transaction:
        header = Transaction(
            household_id=self._household_id,
            id=domain_tx.id,
            date=domain_tx.date,
            description=domain_tx.description,
            reference=domain_tx.reference,
            status=TransactionStatus.PENDING,
            created_by_user_id=domain_tx.created_by_user_id,
            posted_at=datetime.now(tz=UTC) if target_status is TransactionStatus.POSTED else None,
        )
        self._session.add(header)
        self._session.flush()

        for p in domain_tx.postings:
            self._session.add(
                Posting(
                    id=p.id,
                    household_id=self._household_id,
                    transaction_id=header.id,
                    account_id=p.account_id,
                    pool_id=p.pool_id,
                    amount=p.amount.amount,
                    currency=p.amount.currency,
                    fx_rate=p.fx_rate,
                    fx_amount=p.fx_amount.amount if p.fx_amount is not None else None,
                    fx_currency=p.fx_amount.currency if p.fx_amount is not None else None,
                    memo=p.memo,
                )
            )
        self._session.flush()

        if target_status is not TransactionStatus.PENDING:
            # Trigger fires here; aborts if postings don't balance.
            self._session.execute(
                update(Transaction)
                .where(
                    Transaction.household_id == self._household_id,
                    Transaction.id == header.id,
                )
                .values(status=target_status.value)
            )
            header.status = target_status

        return header

    def _force_post_unbalanced_for_test(self, domain_tx: DomainTransaction) -> Transaction:
        """Force-post a (potentially unbalanced) Domain Transaction.

        Bypasses the domain-level balance check that Transaction's
        constructor enforces for POSTED status. Used by trigger tests to
        confirm the DB-level safety net actually fires.
        """
        return self._save(domain_tx, TransactionStatus.POSTED)

    @staticmethod
    def _domain_to_storage(status: DomainTxStatus) -> TransactionStatus:
        return _DOMAIN_TO_STORAGE_STATUS[status.value]
