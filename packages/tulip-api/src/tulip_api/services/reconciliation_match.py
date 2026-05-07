"""Auto-match + complete service for the reconciliation envelope (P5.4.b).

Per ADR-0004 §Q1-Q2 and §Q7, the reconciliation flow is:

1. ``POST /v1/reconciliations`` opens an envelope (status IN_PROGRESS) tied
   to one ``import_batch`` for one ``account``.
2. ``POST /v1/reconciliations/{id}/auto-match`` runs the P5.3 matcher over
   the statement lines + the ledger transactions in the period, persists
   the candidate match rows. Re-running is rejected (locked decision).
3. User reviews + rejects unwanted matches via
   ``POST /v1/reconciliations/{id}/matches/{match_id}/reject``.
4. ``POST /v1/reconciliations/{id}/complete`` validates strict balance
   and, on success, hands off to ``ReconciliationRepository.complete()``
   which denormalises ``reconciled_at`` onto every matched transaction.

The service module deliberately does not commit; the API router wraps a
single ``commit()`` around the audit-log row + the persisted matches so
mid-flight failures roll back atomically.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING

from tulip_core.money import Money
from tulip_core.reconciliation.match_confidence import (
    MatchConfidence as DomainMatchConfidence,
)
from tulip_core.reconciliation.matcher import find_candidates
from tulip_core.reconciliation.statement_line import (
    StatementLine as DomainStatementLine,
)
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import (
    MatchConfidence as StorageMatchConfidence,
)
from tulip_storage.models import (
    ReconciliationStatus,
    TransactionStatus,
)
from tulip_storage.repositories import (
    ImportBatchRepository,
    ReconciliationMatchRepository,
    ReconciliationRepository,
    StatementLineRepository,
    TransactionRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

    from tulip_storage.models import Posting, Reconciliation, Transaction


_MATCHER_VERSION = "v1"

_DOMAIN_TO_STORAGE_STATUS: dict[DomainTxStatus, TransactionStatus] = {
    DomainTxStatus.PENDING: TransactionStatus.PENDING,
    DomainTxStatus.POSTED: TransactionStatus.POSTED,
    DomainTxStatus.RECONCILED: TransactionStatus.RECONCILED,
}

_STORAGE_TO_DOMAIN_STATUS: dict[TransactionStatus, DomainTxStatus] = {
    v: k for k, v in _DOMAIN_TO_STORAGE_STATUS.items()
}

_DOMAIN_TO_STORAGE_CONFIDENCE: dict[DomainMatchConfidence, StorageMatchConfidence] = {
    DomainMatchConfidence.HIGH: StorageMatchConfidence.HIGH,
    DomainMatchConfidence.MEDIUM: StorageMatchConfidence.MEDIUM,
    DomainMatchConfidence.LOW: StorageMatchConfidence.LOW,
}


class AutoMatchAlreadyRunError(ValueError):
    """Raised when auto_match is called on a reconciliation that already has matches."""

    def __init__(self, existing_match_count: int) -> None:
        """Build with the existing-match count for the typed error response."""
        super().__init__(
            f"reconciliation already has {existing_match_count} match(es); "
            "re-running auto-match would create duplicates"
        )
        self.existing_match_count = existing_match_count


class AutoMatchInvalidStateError(ValueError):
    """Raised when auto_match is called on a non-IN_PROGRESS reconciliation."""

    def __init__(self, current_status: ReconciliationStatus) -> None:
        """Build with the current status for the typed error response."""
        super().__init__(
            f"reconciliation is {current_status.value}; "
            "only IN_PROGRESS reconciliations accept auto-match"
        )
        self.current_status = current_status.value


class CompleteUnbalancedError(ValueError):
    """Raised when /complete is called but matched amount != ending - starting."""

    def __init__(self, *, expected_net: Decimal, matched_net: Decimal, residual: Decimal) -> None:
        """Build with the imbalance figures for the typed error response."""
        super().__init__(
            f"reconciliation does not balance: expected_net={expected_net}, "
            f"matched_net={matched_net}, residual={residual}"
        )
        self.expected_net = expected_net
        self.matched_net = matched_net
        self.residual = residual


class CompleteInvalidStateError(ValueError):
    """Raised when /complete is called on a non-IN_PROGRESS reconciliation."""

    def __init__(self, current_status: ReconciliationStatus) -> None:
        """Build with the current status for the typed error response."""
        super().__init__(
            f"reconciliation is {current_status.value}; "
            "only IN_PROGRESS reconciliations may be completed"
        )
        self.current_status = current_status.value


@dataclass(frozen=True, slots=True)
class AutoMatchResult:
    """Summary of a successful ``auto_match`` call."""

    matches_created: int
    high_count: int
    medium_count: int
    low_count: int


@dataclass(frozen=True, slots=True)
class CompleteResult:
    """Summary of a successful ``complete`` call."""

    affected_transaction_count: int


def _line_to_domain(line: object) -> DomainStatementLine:
    """Adapt a storage StatementLine to the domain value object the matcher expects."""
    return DomainStatementLine(
        id=line.id,  # type: ignore[attr-defined]
        import_batch_id=line.import_batch_id,  # type: ignore[attr-defined]
        line_number=line.line_number,  # type: ignore[attr-defined]
        posted_date=line.posted_date,  # type: ignore[attr-defined]
        amount=Money(line.amount, line.currency),  # type: ignore[attr-defined]
        description=line.description,  # type: ignore[attr-defined]
        raw=MappingProxyType({}),
        counterparty=line.counterparty,  # type: ignore[attr-defined]
        reference=line.reference,  # type: ignore[attr-defined]
        fitid=line.fitid,  # type: ignore[attr-defined]
    )


def _tx_to_domain(tx: Transaction, postings: list[Posting]) -> DomainTransaction:
    """Adapt a storage Transaction + its postings to a domain Transaction.

    The matcher iterates ``tx.postings`` looking for the bank-side posting,
    so postings must accompany the header. PENDING transactions are
    upcast to a "posted-shape" only when balanced; the matcher's own
    status filter (``POSTED``/``RECONCILED``) excludes PENDING from
    eligibility, so we rely on that rather than re-validating here.
    """
    domain_postings = tuple(
        DomainPosting(
            id=p.id,
            account_id=p.account_id,
            amount=Money(p.amount, p.currency),
            pool_id=p.pool_id,
        )
        for p in postings
    )
    return DomainTransaction(
        id=tx.id,
        household_id=tx.household_id,
        date=tx.date,
        description=tx.description,
        reference=tx.reference,
        postings=domain_postings,
        status=_STORAGE_TO_DOMAIN_STATUS[tx.status],
    )


async def auto_match(
    *,
    session: Session,
    household_id: UUID,
    reconciliation: Reconciliation,
    actor_user_id: UUID | None,
) -> AutoMatchResult:
    """Run the P5.3 matcher over the reconciliation's batch + ledger txs.

    Persists every emitted ``CandidateMatch`` as a ``reconciliation_matches``
    row with confidence + ``matcher_version="v1"``.

    Raises:
        AutoMatchInvalidStateError: ``reconciliation.status`` is not IN_PROGRESS.
        AutoMatchAlreadyRunError: this reconciliation already has matches.

    """
    if reconciliation.status is not ReconciliationStatus.IN_PROGRESS:
        raise AutoMatchInvalidStateError(reconciliation.status)

    match_repo = ReconciliationMatchRepository(session, household_id)
    existing = match_repo.list_for_reconciliation(reconciliation.id)
    if existing:
        raise AutoMatchAlreadyRunError(existing_match_count=len(existing))

    # Statement lines from the source batch (non-excluded only — the matcher
    # has no view of is_excluded; if we passed them, an excluded line could
    # match by accident and the user would have to re-reject).
    lines_repo = StatementLineRepository(session, household_id)
    raw_lines = lines_repo.list_for_batch(reconciliation.source_import_batch_id)  # type: ignore[arg-type]
    eligible_lines = [line for line in raw_lines if not line.is_excluded]
    domain_lines = [_line_to_domain(line) for line in eligible_lines]

    # Ledger transactions in the statement window for the account.
    tx_repo = TransactionRepository(session, household_id)
    headers = tx_repo.list_headers(
        account_id=reconciliation.account_id,
        from_date=reconciliation.statement_period_start,
        to_date=reconciliation.statement_period_end,
        status=TransactionStatus.POSTED,
    )
    domain_txs: list[DomainTransaction] = []
    for header in headers:
        postings = tx_repo.list_postings(header.id)
        domain_txs.append(_tx_to_domain(header, postings))

    # Already-reconciled set: any tx in this household with a non-NULL
    # reconciliation_id (set only by ReconciliationRepository.complete()).
    # The matcher's date-window pre-filter limits the set we need to check.
    reconciled_ids = frozenset(h.id for h in headers if h.reconciliation_id is not None)

    candidates = find_candidates(
        domain_lines,
        domain_txs,
        account_id=reconciliation.account_id,
        reconciled_transaction_ids=reconciled_ids,
    )

    high = medium = low = 0
    for candidate in candidates:
        match_repo.create(
            reconciliation_id=reconciliation.id,
            statement_line_id=candidate.statement_line_id,
            ledger_transaction_id=candidate.ledger_transaction_id,
            match_amount=candidate.match_amount.amount,
            currency=candidate.match_amount.currency,
            confidence=_DOMAIN_TO_STORAGE_CONFIDENCE[candidate.confidence],
            matcher_version=_MATCHER_VERSION,
            created_by_user_id=None,  # matcher-produced — null per ADR §Q9
        )
        if candidate.confidence is DomainMatchConfidence.HIGH:
            high += 1
        elif candidate.confidence is DomainMatchConfidence.MEDIUM:
            medium += 1
        else:
            low += 1

    del actor_user_id  # currently unused — auto-match rows have null user
    return AutoMatchResult(
        matches_created=len(candidates),
        high_count=high,
        medium_count=medium,
        low_count=low,
    )


def complete(
    *,
    session: Session,
    household_id: UUID,
    reconciliation: Reconciliation,
) -> CompleteResult:
    """Validate strict balance, then hand off to the storage chokepoint.

    Per the locked decision: ``sum(match.match_amount) == ending - starting``.
    Excluded lines + carry-forward (P5.4.c) reduce the expected net but
    are out of scope for P5.4.b — if the user has excluded any lines, the
    balance check will likely fail and they must un-exclude.

    Raises:
        CompleteInvalidStateError: ``reconciliation.status`` is not IN_PROGRESS.
        CompleteUnbalancedError: matched net != expected net.

    """
    if reconciliation.status is not ReconciliationStatus.IN_PROGRESS:
        raise CompleteInvalidStateError(reconciliation.status)

    match_repo = ReconciliationMatchRepository(session, household_id)
    matches = match_repo.list_for_reconciliation(reconciliation.id)
    matched_net = sum((m.match_amount for m in matches), Decimal("0"))
    expected_net = (
        reconciliation.statement_ending_balance - reconciliation.statement_starting_balance
    )
    residual = expected_net - matched_net
    if residual != 0:
        raise CompleteUnbalancedError(
            expected_net=expected_net,
            matched_net=matched_net,
            residual=residual,
        )

    ReconciliationRepository(session, household_id).complete(reconciliation.id)
    return CompleteResult(affected_transaction_count=len(matches))


def _account_currency_for_batch(*, session: Session, household_id: UUID, batch_id: UUID) -> str:
    """Look up the account currency on an import batch (used for create-time validation)."""
    batch = ImportBatchRepository(session, household_id).get(batch_id)
    if batch is None:
        raise LookupError(f"import_batch {batch_id} not found in household {household_id}")
    from tulip_storage.repositories import AccountRepository

    account = AccountRepository(session, household_id).get(batch.account_id)
    if account is None:  # pragma: no cover — FK-enforced
        raise LookupError(f"account {batch.account_id} not found")
    return account.currency
