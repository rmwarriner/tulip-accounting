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

from sqlalchemy import String, case, cast, delete, func, select, update

from tulip_storage.encryption import decrypt_field, encrypt_field, field_aad
from tulip_storage.models import Posting, StatementLine, Transaction, TransactionStatus

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from tulip_core.transactions import Posting as DomainPosting
    from tulip_core.transactions import Transaction as DomainTransaction
    from tulip_core.transactions import TransactionStatus as DomainTxStatus


class _Unset:
    """Sentinel — distinguishes "field omitted from PATCH" from "set to None"."""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        """Singleton: ``UNSET is UNSET`` always holds."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"


UNSET: _Unset = _Unset()


class MasterKeyRequiredError(RuntimeError):
    """Raised when a notes write is attempted without a master key configured."""


class TransactionAlreadyVoidedError(ValueError):
    """Raised when a void is attempted on a transaction that already has a reversal."""


class TransactionNotVoidableError(ValueError):
    """Raised when void is attempted on a transaction that isn't POSTED / RECONCILED."""


class TransactionNotEditableError(ValueError):
    """Raised when PATCH is attempted on a non-PENDING transaction."""


class TransactionNotRectifiableError(ValueError):
    """Raised when rectification is attempted on a non-ledger transaction (#242)."""


class TransactionNotDeletableError(ValueError):
    """Raised when hard-delete is attempted on a non-PENDING transaction."""


_DOMAIN_TO_STORAGE_STATUS: dict[str, TransactionStatus] = {
    "pending": TransactionStatus.PENDING,
    "posted": TransactionStatus.POSTED,
    "reconciled": TransactionStatus.RECONCILED,
}

#: Statuses whose postings count toward the ledger. Pending transactions
#: are workflow state, not ledger state, and are excluded from balances.
_LEDGER_STATUSES = (TransactionStatus.POSTED, TransactionStatus.RECONCILED)

#: The ledger statuses plus PENDING — the filter used when a caller opts
#: into the "what if all pending is real" view (#274).
_LEDGER_PLUS_PENDING = (*_LEDGER_STATUSES, TransactionStatus.PENDING)


def _balance_statuses(*, include_pending: bool) -> tuple[TransactionStatus, ...]:
    """Pick the status filter for a balance query (#274)."""
    return _LEDGER_PLUS_PENDING if include_pending else _LEDGER_STATUSES


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    """One row of a per-(account, currency) trial-balance result.

    ``has_pending`` is True when ``include_pending`` was requested *and*
    at least one PENDING transaction contributed to this row's balance —
    it's what the CLI uses to flag a row with a ``(P)`` marker. It's
    always False on the posted-only path.
    """

    account_id: UUID
    currency: str
    balance: Decimal
    has_pending: bool = False


class TransactionRepository:
    """Persists Transactions and queries existing ones, scoped to one household."""

    def __init__(
        self,
        session: Session,
        household_id: UUID,
        *,
        master_key: bytes | None = None,
    ) -> None:
        """Bind the repository to a session and a tenant scope.

        ``master_key`` is required only when the caller passes a ``notes``
        plaintext to ``save_balanced`` / ``update_pending`` (or reads it
        back via :meth:`decrypt_notes`). Read-only and non-notes write
        paths work without one — keeping the legions of existing callers
        (reconciliation, reports, balance queries) untouched.
        """
        self._session = session
        self._household_id = household_id
        self._master_key = master_key

    def _require_master_key(self) -> bytes:
        if self._master_key is None:
            raise MasterKeyRequiredError(
                "TransactionRepository requires a master_key to encrypt or "
                "decrypt transaction notes; construct with master_key=..."
            )
        return self._master_key

    def _notes_aad(self, tx_id: UUID) -> bytes:
        return field_aad(
            table="transactions",
            column="notes_encrypted",
            household_id=self._household_id,
            row_id=tx_id,
        )

    def decrypt_notes(self, header: Transaction) -> str | None:
        """Return the plaintext notes for ``header`` or None when unset.

        Decoded as UTF-8. Requires a configured master key.
        """
        if header.notes_encrypted is None:
            return None
        key = self._require_master_key()
        return decrypt_field(header.notes_encrypted, key, aad=self._notes_aad(header.id)).decode(
            "utf-8"
        )

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
        id_prefix: str | None = None,
        limit: int | None = None,
    ) -> list[Transaction]:
        """List transaction headers in this household, newest first.

        Filters compose with AND. ``account_id`` matches any transaction
        with at least one posting on that account (any currency). Date
        filters are inclusive on both ends. ``id_prefix`` matches
        transactions whose string-rendered UUID begins with the given
        hex prefix (case-insensitive). ``limit`` caps the number of
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
        if id_prefix is not None:
            # Cast bypasses the GUID type decorator (which would try to
            # parse the LIKE pattern as a UUID). Stored ids are always
            # lowercased by ``str(UUID(...))`` in ``GUID.process_bind_param``,
            # so a lowercased prefix gives case-insensitive matching.
            query = query.where(cast(Transaction.id, String).like(f"{id_prefix.lower()}%"))
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
        include_pending: bool = False,
    ) -> Decimal:
        """Sum the ledger postings on ``account_id`` in ``currency``.

        By default only POSTED + RECONCILED contribute. ``include_pending``
        (#274) widens the sum to PENDING transactions too — the "what if
        all pending is real" view. ``as_of`` limits to transactions on or
        before that date; ``None`` means "all time."
        """
        query = (
            select(func.coalesce(func.sum(Posting.amount), 0))
            .join(Transaction, Transaction.id == Posting.transaction_id)
            .where(
                Posting.household_id == self._household_id,
                Posting.account_id == account_id,
                Posting.currency == currency,
                Transaction.status.in_(_balance_statuses(include_pending=include_pending)),
            )
        )
        if as_of is not None:
            query = query.where(Transaction.date <= as_of)
        result = self._session.execute(query).scalar_one()
        # SQLite returns int 0 from ``coalesce(..., 0)`` even when the
        # column is NUMERIC; normalize to Decimal so callers don't see
        # a type drift on the empty-result branch.
        return Decimal(str(result))

    def count_pending_for_account(
        self,
        account_id: UUID,
        *,
        currency: str,
        as_of: date_type | None = None,
    ) -> int:
        """Count distinct PENDING transactions with a posting on this account.

        Drives the ``pending_count`` field on the account-balance
        response (#274). ``distinct`` because one transaction can carry
        more than one posting on the same account.
        """
        query = (
            select(func.count(func.distinct(Transaction.id)))
            .join(Posting, Posting.transaction_id == Transaction.id)
            .where(
                Posting.household_id == self._household_id,
                Posting.account_id == account_id,
                Posting.currency == currency,
                Transaction.status == TransactionStatus.PENDING,
            )
        )
        if as_of is not None:
            query = query.where(Transaction.date <= as_of)
        return int(self._session.execute(query).scalar_one())

    def count_pending_transactions(self, *, as_of: date_type | None = None) -> int:
        """Count PENDING transactions household-wide (#274).

        Drives the ``pending_count`` field on the trial-balance response.
        """
        query = select(func.count()).where(
            Transaction.household_id == self._household_id,
            Transaction.status == TransactionStatus.PENDING,
        )
        if as_of is not None:
            query = query.where(Transaction.date <= as_of)
        return int(self._session.execute(query).scalar_one())

    def trial_balance(
        self,
        *,
        as_of: date_type | None = None,
        include_pending: bool = False,
    ) -> list[TrialBalanceRow]:
        """Return one row per (account_id, currency) for the household's ledger.

        By default only POSTED + RECONCILED contribute. ``include_pending``
        (#274) widens the sum to PENDING transactions too, and sets
        ``has_pending`` on each row that drew at least one PENDING
        posting. ``as_of`` filters to transactions on or before that
        date; ``None`` means "all time." Accounts with no postings (or
        only zero-net postings) still appear when they have any matching
        posting at all — callers may filter zeros if they want.
        """
        # MAX over a 0/1 case expression collapses to "any pending in the
        # group" — one extra column, no second query.
        is_pending = case(
            (Transaction.status == TransactionStatus.PENDING, 1),
            else_=0,
        )
        query = (
            select(
                Posting.account_id,
                Posting.currency,
                func.coalesce(func.sum(Posting.amount), 0).label("balance"),
                func.max(is_pending).label("has_pending"),
            )
            .join(Transaction, Transaction.id == Posting.transaction_id)
            .where(
                Posting.household_id == self._household_id,
                Transaction.status.in_(_balance_statuses(include_pending=include_pending)),
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
                has_pending=include_pending and bool(has_pending),
            )
            for account_id, currency, balance, has_pending in rows
        ]

    def save_balanced(
        self,
        domain_tx: DomainTransaction,
        *,
        imported_from_id: UUID | None = None,
        notes: str | None = None,
    ) -> Transaction:
        """Persist a balanced Domain Transaction.

        Inserts the header as PENDING, then all postings, then UPDATEs the
        header to the requested final status. The balance trigger validates
        on the UPDATE; if postings are unbalanced the transaction aborts.

        ``imported_from_id`` links the persisted row to the
        ``import_batches`` row that produced it (used by the apply /
        promote flow in P5.4.a).

        ``notes`` is the transaction-level annotation (free text). When
        provided, it is AES-256-GCM-encrypted with the constructor's
        master key and stored in ``notes_encrypted``; ``None`` leaves
        the column unset. Passing a non-None ``notes`` without a
        configured master key raises :class:`MasterKeyRequiredError`.
        """
        target_status = self._domain_to_storage(domain_tx.status)
        return self._save(
            domain_tx,
            target_status,
            imported_from_id=imported_from_id,
            notes=notes,
        )

    def _save(
        self,
        domain_tx: DomainTransaction,
        target_status: TransactionStatus,
        *,
        imported_from_id: UUID | None = None,
        notes: str | None = None,
    ) -> Transaction:
        notes_blob: bytes | None = None
        if notes is not None:
            key = self._require_master_key()
            notes_blob = encrypt_field(
                notes.encode("utf-8"), key, aad=self._notes_aad(domain_tx.id)
            )

        header = Transaction(
            household_id=self._household_id,
            id=domain_tx.id,
            date=domain_tx.date,
            description=domain_tx.description,
            reference=domain_tx.reference,
            status=TransactionStatus.PENDING,
            created_by_user_id=domain_tx.created_by_user_id,
            posted_at=datetime.now(tz=UTC) if target_status is TransactionStatus.POSTED else None,
            imported_from_id=imported_from_id,
            notes_encrypted=notes_blob,
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

    def persist_reversal(
        self,
        source_id: UUID,
        reversal: DomainTransaction,
        *,
        voided_at: datetime,
    ) -> Transaction:
        """Persist a reversal sibling and link the source's voided_by_transaction_id.

        The caller is expected to have already constructed ``reversal`` via
        :func:`tulip_core.accounting.build_reversal` and validated the
        period gate via :func:`tulip_core.accounting.post_transaction`.

        Raises:
            LookupError: ``source_id`` does not exist in this household.
            TransactionAlreadyVoidedError: source is already voided.
            TransactionNotVoidableError: source is PENDING (not in the ledger).

        """
        source = self.get(source_id)
        if source is None:
            raise LookupError(
                f"transaction {source_id} not found in household {self._household_id}"
            )
        if source.voided_by_transaction_id is not None:
            raise TransactionAlreadyVoidedError(
                f"transaction {source_id} already voided by {source.voided_by_transaction_id}"
            )
        if source.status not in _LEDGER_STATUSES:
            raise TransactionNotVoidableError(
                f"transaction {source_id} is {source.status.value}; "
                "only POSTED / RECONCILED transactions may be voided"
            )

        # Persist the reversal sibling. _save handles the PENDING-then-UPDATE
        # dance the balance trigger requires.
        target = self._domain_to_storage(reversal.status)
        self._save(reversal, target)

        # Link the source row.
        self._session.execute(
            update(Transaction)
            .where(
                Transaction.household_id == self._household_id,
                Transaction.id == source_id,
            )
            .values(
                voided_by_transaction_id=reversal.id,
                voided_at=voided_at,
            )
        )
        return self._session.get(Transaction, (self._household_id, reversal.id))  # type: ignore[return-value]

    def delete_pending(self, tx_id: UUID) -> None:
        """Hard-delete a PENDING transaction and its postings.

        When the transaction was created via ``imports apply`` (i.e. there
        is a ``statement_lines.promoted_transaction_id`` row referencing
        it), the back-reference is NULLed before the transaction delete
        (#301). Semantically this **un-promotes** the source line — it
        returns to the unmatched pool so the operator can re-promote or
        exclude it. Without this NULLing the composite RESTRICT FK
        ``fk_statement_lines_promoted_tx`` would block the delete with
        an IntegrityError. Other FKs into ``transactions`` for PENDING
        rows are unreachable by construction: ``reconciliation_matches``
        only references POSTED/RECONCILED transactions (the matcher
        rejects PENDING), and ``transactions.voided_by_transaction_id``
        likewise only links from POSTED rows (void requires POSTED).

        Raises:
            LookupError: ``tx_id`` does not exist in this household.
            TransactionNotDeletableError: transaction is not PENDING.

        """
        existing = self.get(tx_id)
        if existing is None:
            raise LookupError(f"transaction {tx_id} not found in household {self._household_id}")
        if existing.status is not TransactionStatus.PENDING:
            raise TransactionNotDeletableError(
                f"transaction {tx_id} is {existing.status.value}; "
                "only PENDING transactions may be hard-deleted (use void otherwise)"
            )
        # Un-promote any statement line that points at this transaction
        # (#301). The FK is RESTRICT; without this, the DELETE below
        # raises sqlite3.IntegrityError.
        self._session.execute(
            update(StatementLine)
            .where(
                StatementLine.household_id == self._household_id,
                StatementLine.promoted_transaction_id == tx_id,
            )
            .values(promoted_transaction_id=None)
        )
        self._session.execute(
            delete(Posting).where(
                Posting.household_id == self._household_id,
                Posting.transaction_id == tx_id,
            )
        )
        self._session.execute(
            delete(Transaction).where(
                Transaction.household_id == self._household_id,
                Transaction.id == tx_id,
            )
        )

    def update_pending(
        self,
        tx_id: UUID,
        *,
        date: date_type,
        description: str,
        reference: str | None,
        postings: tuple[DomainPosting, ...],
        notes: str | None | _Unset = UNSET,
    ) -> Transaction:
        """Update fields and replace postings on a PENDING transaction.

        ``notes`` follows PATCH semantics: ``UNSET`` (the default) leaves
        the column unchanged; an explicit ``None`` clears it; a string
        encrypts and stores the new plaintext. A non-UNSET, non-None
        ``notes`` requires a configured master key.

        Raises:
            LookupError: ``tx_id`` does not exist in this household.
            TransactionNotEditableError: transaction is not PENDING.
            MasterKeyRequiredError: notes is a string but no master_key.

        """
        existing = self.get(tx_id)
        if existing is None:
            raise LookupError(f"transaction {tx_id} not found in household {self._household_id}")
        if existing.status is not TransactionStatus.PENDING:
            raise TransactionNotEditableError(
                f"transaction {tx_id} is {existing.status.value}; "
                "only PENDING transactions may be edited (use void otherwise)"
            )
        header_values: dict[str, object] = {
            "date": date,
            "description": description,
            "reference": reference,
        }
        if not isinstance(notes, _Unset):
            if notes is None:
                header_values["notes_encrypted"] = None
            else:
                key = self._require_master_key()
                header_values["notes_encrypted"] = encrypt_field(
                    notes.encode("utf-8"), key, aad=self._notes_aad(tx_id)
                )
        self._session.execute(
            update(Transaction)
            .where(
                Transaction.household_id == self._household_id,
                Transaction.id == tx_id,
            )
            .values(**header_values)
        )
        # Replace postings wholesale: simpler than diff-merge and the trigger
        # is a no-op on PENDING transactions.
        self._session.execute(
            delete(Posting).where(
                Posting.household_id == self._household_id,
                Posting.transaction_id == tx_id,
            )
        )
        for p in postings:
            self._session.add(
                Posting(
                    id=p.id,
                    household_id=self._household_id,
                    transaction_id=tx_id,
                    account_id=p.account_id,
                    pool_id=p.pool_id,
                    amount=p.amount.amount,
                    currency=p.amount.currency,
                    fx_rate=p.fx_rate,
                    fx_amount=p.fx_amount.amount if p.fx_amount is not None else None,
                    fx_currency=(p.fx_amount.currency if p.fx_amount is not None else None),
                    memo=p.memo,
                )
            )
        self._session.flush()
        refreshed = self._session.get(Transaction, (self._household_id, tx_id))
        assert refreshed is not None  # noqa: S101 - existence verified above
        return refreshed

    def rectify_posted(
        self,
        tx_id: UUID,
        *,
        description: str | _Unset = UNSET,
        reference: str | None | _Unset = UNSET,
        notes: str | None | _Unset = UNSET,
    ) -> tuple[Transaction, UUID | None]:
        """Rectify a POSTED / RECONCILED transaction's header fields (#242).

        Mutates ``description`` / ``reference`` / ``notes_encrypted`` on the
        row in place — postings, status, and date are untouched. Each field
        follows PATCH semantics via the ``UNSET`` sentinel.

        When the source has a paired reversal (``voided_by_transaction_id``)
        and the reversal's description still matches the canonical
        ``f"Reversal of {old_description}: "`` prefix the void route writes,
        the reversal's description is rewritten in place so the old
        description doesn't survive at rest in the reversal row. The
        returned UUID is the reversal that was rewritten (or ``None`` when
        no rewrite was applicable).

        Raises:
            LookupError: ``tx_id`` does not exist in this household.
            TransactionNotRectifiableError: row is PENDING.
            MasterKeyRequiredError: notes is a non-None string but no master_key.

        """
        existing = self.get(tx_id)
        if existing is None:
            raise LookupError(f"transaction {tx_id} not found in household {self._household_id}")
        if existing.status not in _LEDGER_STATUSES:
            raise TransactionNotRectifiableError(
                f"transaction {tx_id} is {existing.status.value}; "
                "only POSTED / RECONCILED transactions can be rectified"
            )

        old_description = existing.description
        header_values: dict[str, object] = {}
        if not isinstance(description, _Unset):
            header_values["description"] = description
        if not isinstance(reference, _Unset):
            header_values["reference"] = reference
        if not isinstance(notes, _Unset):
            if notes is None:
                header_values["notes_encrypted"] = None
            else:
                key = self._require_master_key()
                header_values["notes_encrypted"] = encrypt_field(
                    notes.encode("utf-8"), key, aad=self._notes_aad(tx_id)
                )

        if header_values:
            self._session.execute(
                update(Transaction)
                .where(
                    Transaction.household_id == self._household_id,
                    Transaction.id == tx_id,
                )
                .values(**header_values)
            )

        reversal_id_rewritten: UUID | None = None
        if not isinstance(description, _Unset) and existing.voided_by_transaction_id is not None:
            # The void route at transactions.py:353 builds the reversal
            # description as ``f"Reversal of {source.description}: {reason}"``.
            # If that prefix still matches, rewrite it so the source's
            # pre-rectification description doesn't survive at rest.
            reversal_id = existing.voided_by_transaction_id
            reversal = self._session.get(Transaction, (self._household_id, reversal_id))
            if reversal is not None:
                prefix = f"Reversal of {old_description}: "
                if reversal.description.startswith(prefix):
                    suffix = reversal.description[len(prefix) :]
                    new_reversal_description = f"Reversal of [redacted]: {suffix}"
                    self._session.execute(
                        update(Transaction)
                        .where(
                            Transaction.household_id == self._household_id,
                            Transaction.id == reversal_id,
                        )
                        .values(description=new_reversal_description)
                    )
                    reversal_id_rewritten = reversal_id

        self._session.flush()
        refreshed = self._session.get(Transaction, (self._household_id, tx_id))
        assert refreshed is not None  # noqa: S101 - existence verified above
        return refreshed, reversal_id_rewritten

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
