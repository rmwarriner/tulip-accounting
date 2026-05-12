"""Apply / promote service: turns parsed statement lines into PENDING ledger txns.

Per ADR-0004 §Q4, the import flow has two terminal user-actions:

- **Apply** the whole batch — every non-excluded line in the batch is
  promoted into a PENDING ledger transaction in one atomic step. The
  ``import_batches.status`` flips to ``APPLIED`` on success.
- **Promote** a single line — useful for line-by-line review or for
  re-running after fixing per-household configuration (e.g. seeding a
  missing categorizer account).

Each promotion creates exactly one PENDING transaction with two
postings:

- The bank-side posting on the import batch's account, signed as the
  statement line's amount.
- The other-side posting on the account resolved from the registered
  ``Categorizer``'s suggestion. v1's ``NullCategorizer`` always returns
  ``Imbalance:Unknown`` — the user re-categorizes during reconciliation
  review.

The service module deliberately does not commit. Callers (the API
router) wrap it in their own commit/audit transaction so the audit
log row + the promoted-tx rows land atomically.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tulip_core.money import Money
from tulip_core.reconciliation.categorizer import HouseholdContext
from tulip_core.reconciliation.statement_line import StatementLine as DomainStatementLine
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import ImportBatchStatus
from tulip_storage.repositories import (
    AccountRepository,
    ImportBatchRepository,
    StatementLineRepository,
    TransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_core.reconciliation.categorizer import Categorizer
    from tulip_storage.models import ImportBatch, StatementLine, Transaction


class BatchAlreadyAppliedError(ValueError):
    """Raised when apply_batch is called on a batch that's not PARSED."""


class LineAlreadyPromotedError(ValueError):
    """Raised when promote_statement_line is called on an already-promoted line."""


class LineExcludedError(ValueError):
    """Raised when promote_statement_line is called on an is_excluded line."""


class CategorizeUnknownAccountError(ValueError):
    """Raised when the categorizer returns an account_code with no matching Account."""

    def __init__(self, account_code: str, household_id: UUID) -> None:
        """Build with the bad code + household for caller-side rendering."""
        super().__init__(
            f"categorizer returned account_code={account_code!r} but no account "
            f"with that code exists in household {household_id}"
        )
        self.account_code = account_code
        self.household_id = household_id


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Summary of a successful ``apply_batch`` call."""

    batch_id: UUID
    created_count: int
    skipped_count: int
    transaction_ids: tuple[UUID, ...]


def _to_domain_line(line: StatementLine) -> DomainStatementLine:
    """Adapt a storage StatementLine to the domain value object the categorizer expects."""
    return DomainStatementLine(
        id=line.id,
        import_batch_id=line.import_batch_id,
        line_number=line.line_number,
        posted_date=line.posted_date,
        amount=Money(line.amount, line.currency),
        description=line.description,
        raw=MappingProxyType({}),
        counterparty=line.counterparty,
        reference=line.reference,
        fitid=line.fitid,
    )


async def promote_statement_line(
    *,
    session: Session,
    household_id: UUID,
    batch: ImportBatch,
    line: StatementLine,
    categorizer: Categorizer,
    actor_user_id: UUID | None,
) -> Transaction:
    """Promote one statement line into a PENDING ledger Transaction.

    Raises:
        LineExcludedError: ``line.is_excluded`` is True (caller should
            un-exclude first).
        LineAlreadyPromotedError: ``line.promoted_transaction_id`` is set.
        CategorizeUnknownAccountError: the categorizer returned an
            account_code that doesn't exist in this household's chart.

    """
    if line.is_excluded:
        raise LineExcludedError(
            f"statement_line {line.id} is excluded; un-exclude before promoting"
        )
    if line.promoted_transaction_id is not None:
        raise LineAlreadyPromotedError(
            f"statement_line {line.id} already promoted to "
            f"transaction {line.promoted_transaction_id}"
        )

    accounts = AccountRepository(session, household_id)
    bank_account = accounts.get(batch.account_id)
    if bank_account is None:  # pragma: no cover - bank account is FK-enforced
        raise LookupError(f"batch.account_id {batch.account_id} not found")

    domain_line = _to_domain_line(line)
    suggestion = await categorizer.categorize(
        domain_line,
        HouseholdContext(household_id=household_id, account_whitelist=frozenset()),
        session=session,
    )
    other_account = accounts.get_by_code(suggestion.account_code)
    if other_account is None:
        raise CategorizeUnknownAccountError(suggestion.account_code, household_id)

    bank_amount = Money(line.amount, line.currency)
    other_amount = Money(-line.amount, line.currency)
    domain_tx = DomainTransaction(
        id=uuid4(),
        household_id=household_id,
        date=line.posted_date,
        description=line.description,
        postings=(
            DomainPosting(id=uuid4(), account_id=bank_account.id, amount=bank_amount),
            DomainPosting(id=uuid4(), account_id=other_account.id, amount=other_amount),
        ),
        status=DomainTxStatus.PENDING,
        created_by_user_id=actor_user_id,
    )
    tx = TransactionRepository(session, household_id).save_balanced(
        domain_tx, imported_from_id=batch.id
    )
    StatementLineRepository(session, household_id).mark_promoted(line.id, tx.id)
    return tx


async def apply_batch(
    *,
    session: Session,
    household_id: UUID,
    batch: ImportBatch,
    categorizer: Categorizer,
    actor_user_id: UUID | None,
) -> ApplyResult:
    """Promote every applicable line in ``batch``, then mark batch APPLIED.

    "Applicable" = not excluded and not already promoted. Excluded and
    already-promoted lines are silently skipped (counted in
    ``skipped_count``).

    Idempotency is at the batch level: a batch in ``APPLIED`` state
    cannot be re-applied (raises). The caller can re-promote individual
    lines via :func:`promote_statement_line` if needed.

    Atomicity is the caller's responsibility — this function only
    flushes; the caller wraps in a single ``session.commit()`` so a
    mid-batch failure rolls back every promotion.

    Raises:
        BatchAlreadyAppliedError: ``batch.status`` is not ``PARSED``.

    """
    if batch.status is not ImportBatchStatus.PARSED:
        raise BatchAlreadyAppliedError(
            f"import_batch {batch.id} is {batch.status.value}; only PARSED batches may be applied"
        )

    lines_repo = StatementLineRepository(session, household_id)
    transaction_ids: list[UUID] = []
    skipped = 0
    for line in lines_repo.list_for_batch(batch.id):
        if line.is_excluded or line.promoted_transaction_id is not None:
            skipped += 1
            continue
        tx = await promote_statement_line(
            session=session,
            household_id=household_id,
            batch=batch,
            line=line,
            categorizer=categorizer,
            actor_user_id=actor_user_id,
        )
        transaction_ids.append(tx.id)

    ImportBatchRepository(session, household_id).mark_applied(batch.id)
    return ApplyResult(
        batch_id=batch.id,
        created_count=len(transaction_ids),
        skipped_count=skipped,
        transaction_ids=tuple(transaction_ids),
    )
