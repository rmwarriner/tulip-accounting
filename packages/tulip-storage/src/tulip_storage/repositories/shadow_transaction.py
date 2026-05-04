"""ShadowTransactionRepository — persists balanced shadow transactions and queries balances.

Mirrors :class:`tulip_storage.repositories.TransactionRepository` for the
parallel ledger. The save flow inserts the header as PENDING, adds every
shadow posting, then UPDATEs the header to POSTED — the trigger validates
balance on the status transition. ``balance_for_pool`` sums the postings
on a single pool; voided shadow transactions are excluded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, update

from tulip_storage.models import (
    ShadowPosting,
    ShadowTransaction,
    ShadowTxStatus,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_core.allocation import ShadowTransaction as DomainShadowTx
    from tulip_core.allocation import ShadowTxStatus as DomainShadowStatus


_DOMAIN_TO_STORAGE_STATUS: dict[str, ShadowTxStatus] = {
    "pending": ShadowTxStatus.PENDING,
    "posted": ShadowTxStatus.POSTED,
    "voided": ShadowTxStatus.VOIDED,
}

#: Statuses whose postings count toward derived balances. Pending shadow
#: transactions are workflow state; voided are reversed; both are excluded
#: from balance sums.
_BALANCE_STATUSES = (ShadowTxStatus.POSTED,)


class ShadowTxNotVoidableError(ValueError):
    """Raised when a shadow tx is not in a voidable state (i.e. PENDING)."""


class ShadowTransactionRepository:
    """Persists shadow transactions and queries pool balances, scoped to a household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, tx_id: UUID) -> ShadowTransaction | None:
        """Return the ShadowTransaction header by id, or None."""
        return self._session.execute(
            select(ShadowTransaction).where(
                ShadowTransaction.household_id == self._household_id,
                ShadowTransaction.id == tx_id,
            )
        ).scalar_one_or_none()

    def inflow_since(
        self,
        *,
        currency: str,
        since: date_type,
    ) -> Decimal:
        """Sum POSTED ``BUDGET_INFLOW`` shadow tx for ``currency`` since ``since``.

        Used by the envelope-refill handler (P4.3.b) to compute
        ``recent_inflow`` for ``PERCENTAGE_OF_INCOME`` rules. Sums the
        positive (Unallocated) leg of every BUDGET_INFLOW shadow tx
        whose date is on or after ``since``. Pending and voided shadow
        txs don't contribute.

        Returns 0 if no inflow has been declared in the window.
        """
        from tulip_storage.models import (
            ShadowPosting,
            ShadowTxReason,
        )

        query = (
            select(func.coalesce(func.sum(ShadowPosting.amount), 0))
            .join(
                ShadowTransaction,
                ShadowTransaction.id == ShadowPosting.shadow_transaction_id,
            )
            .where(
                ShadowPosting.household_id == self._household_id,
                ShadowPosting.currency == currency,
                ShadowPosting.amount > 0,  # Unallocated leg
                ShadowTransaction.reason == ShadowTxReason.BUDGET_INFLOW,
                ShadowTransaction.status.in_(_BALANCE_STATUSES),
                ShadowTransaction.date >= since,
            )
        )
        result = self._session.execute(query).scalar_one()
        return Decimal(str(result))

    def get_paired_id_for_main_tx(self, main_tx_id: UUID) -> UUID | None:
        """Return the id of the shadow tx paired to ``main_tx_id``, or None.

        Lookup keyed on ``paired_main_tx_id``. The pairing rule (ADR-0001)
        guarantees at most one row per main tx, so ``scalar_one_or_none``
        is the right shape.
        """
        return self._session.execute(
            select(ShadowTransaction.id).where(
                ShadowTransaction.household_id == self._household_id,
                ShadowTransaction.paired_main_tx_id == main_tx_id,
            )
        ).scalar_one_or_none()

    def list_postings(self, tx_id: UUID) -> list[ShadowPosting]:
        """Return all shadow postings belonging to a shadow transaction."""
        return list(
            self._session.execute(
                select(ShadowPosting).where(
                    ShadowPosting.household_id == self._household_id,
                    ShadowPosting.shadow_transaction_id == tx_id,
                )
            )
            .scalars()
            .all()
        )

    def balance_for_pool(
        self,
        pool_id: UUID,
        *,
        currency: str | None = None,
        as_of: date_type | None = None,
    ) -> dict[str, Decimal]:
        """Return ``{currency: net amount}`` over POSTED shadow postings for ``pool_id``.

        - ``currency=None`` (the default) returns one row per currency
          touching the pool. Pools are single-currency, so the map will
          almost always have one entry.
        - ``currency="USD"`` filters to that one currency; the map will be
          empty if the pool has no postings in that currency.
        - ``as_of=YYYY-MM-DD`` filters to shadow transactions on or before
          that date.
        """
        query = (
            select(
                ShadowPosting.currency,
                func.coalesce(func.sum(ShadowPosting.amount), 0).label("balance"),
            )
            .join(
                ShadowTransaction,
                ShadowTransaction.id == ShadowPosting.shadow_transaction_id,
            )
            .where(
                ShadowPosting.household_id == self._household_id,
                ShadowPosting.pool_id == pool_id,
                ShadowTransaction.status.in_(_BALANCE_STATUSES),
            )
            .group_by(ShadowPosting.currency)
        )
        if currency is not None:
            query = query.where(ShadowPosting.currency == currency)
        if as_of is not None:
            query = query.where(ShadowTransaction.date <= as_of)
        rows = self._session.execute(query).all()
        return {ccy: Decimal(str(bal)) for ccy, bal in rows}

    def void(self, shadow_tx_id: UUID, *, voided_at: datetime) -> ShadowTransaction:
        """Flip a POSTED shadow transaction's status to VOIDED.

        Voided shadow transactions are excluded from ``balance_for_pool`` and
        ``inflow_since`` so pool balances auto-correct. Idempotent on
        already-voided rows. Used by the main-tx void chokepoint (ADR-0004
        §P5.0) to keep the shadow ledger consistent when a pool-tagged main
        tx is voided.

        The shadow ledger has no period concept and balances are derived,
        so a status flip is sufficient — no sibling reversal needed. The
        ``voided_by_shadow_tx_id`` column stays NULL for this path; it's
        reserved for a future shadow-internal void-via-sibling pattern that
        we don't need today.

        Raises:
            LookupError: shadow tx not found in this household.
            ShadowTxNotVoidableError: shadow tx is PENDING (work-in-progress).

        """
        existing = self.get(shadow_tx_id)
        if existing is None:
            raise LookupError(
                f"shadow_transaction {shadow_tx_id} not found in household {self._household_id}"
            )
        if existing.status is ShadowTxStatus.VOIDED:
            return existing  # idempotent no-op
        if existing.status is not ShadowTxStatus.POSTED:
            raise ShadowTxNotVoidableError(
                f"shadow_transaction {shadow_tx_id} is {existing.status.value}; "
                "only POSTED shadow transactions may be voided"
            )
        self._session.execute(
            update(ShadowTransaction)
            .where(
                ShadowTransaction.household_id == self._household_id,
                ShadowTransaction.id == shadow_tx_id,
            )
            .values(status=ShadowTxStatus.VOIDED.value, voided_at=voided_at)
        )
        # Refresh in-session.
        self._session.expire(existing)
        loaded = self.get(shadow_tx_id)
        assert loaded is not None  # noqa: S101 - just updated above
        return loaded

    def save_balanced(self, domain_tx: DomainShadowTx) -> ShadowTransaction:
        """Persist a balanced domain ShadowTransaction.

        Inserts the header as PENDING, then every shadow posting, then
        UPDATEs the header to the requested final status. The shadow-ledger
        balance trigger validates on the UPDATE.
        """
        target_status = self._domain_to_storage(domain_tx.status)
        return self._save(domain_tx, target_status)

    def _save(
        self,
        domain_tx: DomainShadowTx,
        target_status: ShadowTxStatus,
    ) -> ShadowTransaction:
        # Header inserted as PENDING so the trigger doesn't fire before
        # postings exist. Storage enum values match domain enum values
        # by construction, so we round-trip via .value across the seam.
        from tulip_storage.models import ShadowTxReason as StorageReason

        header = ShadowTransaction(
            household_id=self._household_id,
            id=domain_tx.id,
            date=domain_tx.date,
            description=domain_tx.description,
            reason=StorageReason(domain_tx.reason.value),
            status=ShadowTxStatus.PENDING,
            paired_main_tx_id=domain_tx.paired_main_tx_id,
            created_by_user_id=domain_tx.created_by_user_id,
            posted_at=datetime.now(tz=UTC) if target_status is ShadowTxStatus.POSTED else None,
        )
        self._session.add(header)
        self._session.flush()

        for p in domain_tx.postings:
            self._session.add(
                ShadowPosting(
                    id=p.id,
                    household_id=self._household_id,
                    shadow_transaction_id=header.id,
                    pool_id=p.pool_id,
                    amount=p.amount.amount,
                    currency=p.amount.currency,
                    memo=p.memo,
                )
            )
        self._session.flush()

        if target_status is not ShadowTxStatus.PENDING:
            # Trigger fires on the UPDATE; aborts if postings don't balance.
            self._session.execute(
                update(ShadowTransaction)
                .where(
                    ShadowTransaction.household_id == self._household_id,
                    ShadowTransaction.id == header.id,
                )
                .values(status=target_status.value)
            )
            header.status = target_status

        return header

    @staticmethod
    def _domain_to_storage(status: DomainShadowStatus) -> ShadowTxStatus:
        return _DOMAIN_TO_STORAGE_STATUS[status.value]
