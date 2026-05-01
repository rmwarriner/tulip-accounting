"""TransactionRepository — persists balanced domain Transactions to the ledger.

The save flow always writes the header as PENDING first, inserts every
posting, then UPDATEs the header to its final status. The DB trigger
(see migrations 0001) validates balance on the status transition. That
two-phase flow is what lets us insert an entire transaction in one
session.commit() while still having defense-in-depth against unbalanced
writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, update

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

#: Statuses whose postings count toward the ledger. Pending transactions
#: are workflow state, not ledger state, and are excluded from balances.
_LEDGER_STATUSES = (TransactionStatus.POSTED, TransactionStatus.RECONCILED)


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    """One row of a per-(account, currency) trial-balance result."""

    account_id: UUID
    currency: str
    balance: Decimal


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

    def list_headers(
        self,
        *,
        account_id: UUID | None = None,
        from_date: date_type | None = None,
        to_date: date_type | None = None,
        status: TransactionStatus | None = None,
        limit: int | None = None,
    ) -> list[Transaction]:
        """List transaction headers in this household, newest first.

        Filters compose with AND. ``account_id`` matches any transaction
        with at least one posting on that account (any currency). Date
        filters are inclusive on both ends. ``limit`` caps the number of
        rows returned; ``None`` means no cap.
        """
        query = select(Transaction).where(Transaction.household_id == self._household_id)
        if account_id is not None:
            query = query.where(
                Transaction.id.in_(
                    select(Posting.transaction_id).where(
                        Posting.household_id == self._household_id,
                        Posting.account_id == account_id,
                    )
                )
            )
        if from_date is not None:
            query = query.where(Transaction.date >= from_date)
        if to_date is not None:
            query = query.where(Transaction.date <= to_date)
        if status is not None:
            query = query.where(Transaction.status == status)
        query = query.order_by(Transaction.date.desc(), Transaction.created_at.desc())
        if limit is not None:
            query = query.limit(limit)
        return list(self._session.execute(query).scalars().all())

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

    def balance_for_account(
        self,
        account_id: UUID,
        *,
        currency: str,
        as_of: date_type | None = None,
    ) -> Decimal:
        """Sum the ledger postings on ``account_id`` in ``currency``.

        Pending transactions are excluded — only POSTED and RECONCILED
        contribute. ``as_of`` limits to transactions on or before that
        date; ``None`` means "all time."
        """
        query = (
            select(func.coalesce(func.sum(Posting.amount), 0))
            .join(Transaction, Transaction.id == Posting.transaction_id)
            .where(
                Posting.household_id == self._household_id,
                Posting.account_id == account_id,
                Posting.currency == currency,
                Transaction.status.in_(_LEDGER_STATUSES),
            )
        )
        if as_of is not None:
            query = query.where(Transaction.date <= as_of)
        result = self._session.execute(query).scalar_one()
        # SQLite returns int 0 from ``coalesce(..., 0)`` even when the
        # column is NUMERIC; normalize to Decimal so callers don't see
        # a type drift on the empty-result branch.
        return Decimal(str(result))

    def trial_balance(
        self,
        *,
        as_of: date_type | None = None,
    ) -> list[TrialBalanceRow]:
        """Return one row per (account_id, currency) for the household's ledger.

        Pending transactions are excluded. ``as_of`` filters to
        transactions on or before that date; ``None`` means "all time."
        Accounts with no postings (or only zero-net postings) still
        appear when they have any matching posting at all — callers may
        filter zeros if they want.
        """
        query = (
            select(
                Posting.account_id,
                Posting.currency,
                func.coalesce(func.sum(Posting.amount), 0).label("balance"),
            )
            .join(Transaction, Transaction.id == Posting.transaction_id)
            .where(
                Posting.household_id == self._household_id,
                Transaction.status.in_(_LEDGER_STATUSES),
            )
            .group_by(Posting.account_id, Posting.currency)
        )
        if as_of is not None:
            query = query.where(Transaction.date <= as_of)
        rows = self._session.execute(query).all()
        return [
            TrialBalanceRow(
                account_id=account_id,
                currency=currency,
                balance=Decimal(str(balance)),
            )
            for account_id, currency, balance in rows
        ]

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
