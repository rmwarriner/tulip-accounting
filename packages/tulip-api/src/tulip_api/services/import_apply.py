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

import json
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tulip_core.money import Money
from tulip_core.reconciliation.categorizer import HouseholdContext
from tulip_core.reconciliation.statement_line import (
    ParsedSplit,
    ParsedStatementLine,
)
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
from tulip_storage.models import AccountType, ImportBatchStatus
from tulip_storage.repositories import (
    AccountRepository,
    ImportBatchRepository,
    StatementLineRepository,
    TransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_core.reconciliation.categorizer import Categorizer
    from tulip_storage.models import Account, ImportBatch, StatementLine, Transaction


# Stable name + code used for the auto-created Imbalance account
# (one per currency). hledger / ledger-cli use the same name convention,
# so PTA export → external tool → re-import round-trips cleanly.
_IMBALANCE_NAME = "Imbalance:Unknown"
_IMBALANCE_CODE_PREFIX = "9999"  # 9999.<CURRENCY>, e.g. 9999.USD

# Key under which the structured splits envelope lives inside the
# statement-line ``raw_json`` blob (#297). The full shape on disk is::
#
#   {"raw": {"<QIF-key>": "<value>", ...},
#    "__splits__": [
#        {"amount": "-45.27", "currency": "USD",
#         "category": "Needs:Utilities:...", "memo": "Current gas charges"},
#        ...
#    ]}
#
# Lines with no splits omit the ``__splits__`` key. The choice of a
# reserved double-underscore prefix avoids collision with any
# format-native field name on the ``raw`` side.
_RAW_JSON_SPLITS_KEY = "__splits__"
_RAW_JSON_TAGS_KEY = "__tags__"


def serialize_parsed_line_raw_json(parsed: ParsedStatementLine) -> str:
    """Serialize a parsed line for ``statement_lines.raw_json`` (#297 + #447).

    Single chokepoint so every importer uses the same envelope. Lines
    with no splits / no tags get the minimal ``{"raw": {...}}`` blob.
    The ``__tags__`` key stores the per-split tags inline on each
    split entry; the line-level union lives at the envelope top.
    """
    payload: dict[str, object] = {"raw": dict(parsed.raw)}
    if parsed.splits:
        payload[_RAW_JSON_SPLITS_KEY] = [
            {
                "amount": str(s.amount.amount),
                "currency": s.amount.currency,
                "category": s.category,
                "memo": s.memo,
                "tags": list(s.tags),
            }
            for s in parsed.splits
        ]
    if parsed.tags:
        payload[_RAW_JSON_TAGS_KEY] = list(parsed.tags)
    return json.dumps(payload, ensure_ascii=False)


def _extract_line_tags_from_raw_json(raw_json: str) -> tuple[str, ...]:
    """Read the line-level tag tuple from a persisted ``raw_json`` blob (#447)."""
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()
    raw_tags = payload.get(_RAW_JSON_TAGS_KEY)
    if not isinstance(raw_tags, list):
        return ()
    return tuple(t for t in raw_tags if isinstance(t, str) and t)


def _extract_qif_cleared_status_from_raw_json(raw_json: str) -> DomainTxStatus | None:
    """Read the QIF ``C`` (cleared) field out of ``raw_json`` (#279).

    QIF carries per-transaction status in the ``C`` field:

    - empty / missing → ``None`` (caller defaults to PENDING).
    - ``c`` / ``C`` / ``*`` → POSTED. Source software's "cleared" /
      "auto-matched" / "manually cleared in register" state. Maps to
      Tulip's POSTED.
    - ``R`` / ``r`` → RECONCILED. Source software's
      "matched during reconciliation" state.

    Returns ``None`` (use default) when the field is missing, the
    value is unrecognised, the JSON is malformed, or the line was
    imported via a non-QIF source (OFX/CSV have their own status
    semantics; this helper is QIF-only).
    """
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        return None
    c = raw.get("C")
    if not isinstance(c, str):
        return None
    normalised = c.strip().lower()
    if normalised in ("c", "*"):
        return DomainTxStatus.POSTED
    if normalised == "r":
        return DomainTxStatus.RECONCILED
    return None


def _extract_splits_from_raw_json(raw_json: str, *, currency: str) -> tuple[ParsedSplit, ...]:
    """Read the splits tuple back out of a persisted ``raw_json`` blob (#297).

    Returns an empty tuple for lines with no splits, malformed JSON
    (legacy ``str(dict)`` repr from before #297 — already-applied
    rows that won't re-enter the promotion path), or any missing /
    invalid field. ``currency`` is the statement line's currency; we
    use it as a safety check that every split's currency matches the
    parent line (the parser already enforces this at parse time, but
    a hand-edited DB row could violate it).
    """
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()
    raw_splits = payload.get(_RAW_JSON_SPLITS_KEY)
    if not isinstance(raw_splits, list) or not raw_splits:
        return ()
    out: list[ParsedSplit] = []
    for entry in raw_splits:
        if not isinstance(entry, dict):
            return ()
        try:
            amount_str = entry["amount"]
            split_currency = entry["currency"]
            category = entry["category"]
        except KeyError:
            return ()
        memo = entry.get("memo")
        if split_currency != currency:
            return ()
        try:
            amount = Money(Decimal(str(amount_str)), split_currency)
        except (ValueError, TypeError):
            return ()
        raw_tags = entry.get("tags") or []
        tags: tuple[str, ...] = ()
        if isinstance(raw_tags, list):
            tags = tuple(t for t in raw_tags if isinstance(t, str) and t)
        out.append(ParsedSplit(amount=amount, category=category, memo=memo, tags=tags))
    return tuple(out)


def _get_or_create_imbalance_account(
    *,
    session: Session,
    household_id: UUID,
    currency: str,
    actor_user_id: UUID | None,
) -> Account:
    """Return the household's Imbalance:Unknown account for ``currency``.

    Looks up by code ``9999.<CURRENCY>`` first; creates it as an EQUITY
    account if missing. Used by the ``no_categorize`` apply path (#199
    slice B) so users migrating data from another system can land
    everything as PENDING and assign categories manually later.
    """
    accounts = AccountRepository(session, household_id)
    code = f"{_IMBALANCE_CODE_PREFIX}.{currency}"
    existing = accounts.get_by_code(code)
    if existing is not None:
        return existing
    return accounts.create(
        name=_IMBALANCE_NAME,
        type=AccountType.EQUITY,
        currency=currency,
        code=code,
        visibility="shared",
        created_by_user_id=actor_user_id,
    )


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


class PlaceholderAccountError(ValueError):
    """The target account for a posting is a placeholder (#52)."""

    def __init__(self, account_id: str) -> None:
        """Build with the offending account id."""
        super().__init__(
            f"cannot post to placeholder account {account_id}; pick a leaf "
            "or clear the placeholder flag"
        )
        self.account_id = account_id


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
    no_categorize: bool = False,
    as_posted: bool = False,
    treat_cleared_as_pending: bool = False,
) -> Transaction:
    """Promote one statement line into a ledger Transaction.

    ``no_categorize=True`` skips the categorizer entirely and routes the
    other-side posting to the household's ``Imbalance:Unknown`` account
    for the bank account's currency (auto-created on first use). Used
    for bulk migrations from other accounting tools where the user
    wants to assign categories manually after import.

    ``as_posted=True`` lands the new transaction as ``POSTED`` instead
    of the default ``PENDING`` (issue #210). The bank-side + other-side
    postings already sum to zero per currency, so the POSTED balance
    invariant holds. Useful for bulk migrations where every imported
    line is already cleared by the source bank/tool; the user can fix
    categorization later via ``tulip transactions edit`` (which
    transparently void+recreates POSTED transactions).

    **Status resolution priority** (#279):
    1. ``as_posted=True`` → POSTED (highest precedence; force-all-POSTED).
    2. ``treat_cleared_as_pending=True`` → PENDING (force-all-PENDING; the
       legacy "everything pending" behaviour for users who want the
       manual review pass even on lines the source marked cleared).
    3. QIF ``C`` field carried in raw_json → POSTED for ``c``/``*``;
       RECONCILED for ``R``. Default behaviour for QIF imports.
    4. Otherwise → PENDING.

    Raises:
        LineExcludedError: ``line.is_excluded`` is True (caller should
            un-exclude first).
        LineAlreadyPromotedError: ``line.promoted_transaction_id`` is set.
        CategorizeUnknownAccountError: the categorizer returned an
            account_code that doesn't exist in this household's chart.
            Not raised when ``no_categorize=True``.

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
    if bank_account.is_placeholder:
        # #52: a batch's bank account can be flipped to placeholder after
        # the batch is uploaded; reject the apply rather than write a
        # posting against a placeholder. The router lifts this to the
        # account.placeholder_posting Problem Detail.
        raise PlaceholderAccountError(account_id=str(bank_account.id))

    splits = _extract_splits_from_raw_json(line.raw_json, currency=line.currency)
    # Status resolution per #279 (see docstring).
    if as_posted:
        tx_status = DomainTxStatus.POSTED
    elif treat_cleared_as_pending:
        tx_status = DomainTxStatus.PENDING
    else:
        cleared = _extract_qif_cleared_status_from_raw_json(line.raw_json)
        tx_status = cleared if cleared is not None else DomainTxStatus.PENDING

    if splits:
        # #297: split-bearing line promotes to one transaction with
        # ``1 + len(splits)`` postings — one bank-side at the parent
        # total + one per split. Per-split categories come from the
        # QIF ``S`` field (encoded in ``ParsedSplit.category``); we
        # try ``accounts.get_by_code`` first and fall back to the
        # Imbalance account for unknown categories so a missing chart
        # entry doesn't block the whole batch. ``no_categorize`` is
        # not honoured for splits — the source format already
        # categorized them, the operator's "skip categorization" toggle
        # is moot.
        postings: list[DomainPosting] = [
            DomainPosting(
                id=uuid4(),
                account_id=bank_account.id,
                amount=Money(line.amount, line.currency),
            )
        ]
        # #447 follow-up: capture each split's posting id so we can
        # attach the split's tags directly to that posting (not just
        # the parent transaction). Same ordering as ``splits`` for a
        # 1:1 zip below.
        split_posting_ids: list[UUID] = []
        for split in splits:
            # #450: prefer the path-aware resolver so GnuCash-rooted
            # charts (Expenses:Wants:Personal:Gifts) accept Banktivity-
            # style category strings (Wants:Personal:Gifts) without
            # falling through to Imbalance:Unknown. Code lookup stays
            # as the fast path for charts that put colon-paths in the
            # ``code`` column.
            split_account = accounts.get_by_code(split.category)
            if split_account is None:
                split_account = accounts.find_by_name_path(split.category)
            if split_account is None:
                split_account = _get_or_create_imbalance_account(
                    session=session,
                    household_id=household_id,
                    currency=line.currency,
                    actor_user_id=actor_user_id,
                )
            split_posting_id = uuid4()
            split_posting_ids.append(split_posting_id)
            postings.append(
                DomainPosting(
                    id=split_posting_id,
                    account_id=split_account.id,
                    amount=Money(-split.amount.amount, line.currency),
                    memo=split.memo,
                )
            )
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=household_id,
            date=line.posted_date,
            description=line.description,
            postings=tuple(postings),
            status=tx_status,
            created_by_user_id=actor_user_id,
        )
        tx = TransactionRepository(session, household_id).save_balanced(
            domain_tx, imported_from_id=batch.id
        )
        _apply_qif_tags_from_raw_json(
            session=session,
            household_id=household_id,
            transaction_id=tx.id,
            line_raw_json=line.raw_json,
            splits=splits,
        )
        _apply_qif_posting_tags(
            session=session,
            household_id=household_id,
            splits=splits,
            split_posting_ids=split_posting_ids,
        )
        StatementLineRepository(session, household_id).mark_promoted(line.id, tx.id)
        return tx

    if no_categorize:
        other_account = _get_or_create_imbalance_account(
            session=session,
            household_id=household_id,
            currency=line.currency,
            actor_user_id=actor_user_id,
        )
    else:
        domain_line = _to_domain_line(line)
        suggestion = await categorizer.categorize(
            domain_line,
            HouseholdContext(
                household_id=household_id,
                account_whitelist=frozenset(),
                acting_user_id=actor_user_id,
            ),
            session=session,
        )
        # #450: try code first, then hierarchical name-path. The
        # categorizer often returns colon-paths from the chart's name
        # column when the chart has no ``code`` populated (the
        # GnuCash-imported shape).
        resolved = accounts.get_by_code(suggestion.account_code)
        if resolved is None:
            resolved = accounts.find_by_name_path(suggestion.account_code)
        if resolved is None:
            raise CategorizeUnknownAccountError(suggestion.account_code, household_id)
        other_account = resolved

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
        status=tx_status,
        created_by_user_id=actor_user_id,
    )
    tx = TransactionRepository(session, household_id).save_balanced(
        domain_tx, imported_from_id=batch.id
    )
    _apply_qif_tags_from_raw_json(
        session=session,
        household_id=household_id,
        transaction_id=tx.id,
        line_raw_json=line.raw_json,
        splits=(),
    )
    StatementLineRepository(session, household_id).mark_promoted(line.id, tx.id)
    return tx


def _apply_qif_tags_from_raw_json(
    *,
    session: Session,
    household_id: UUID,
    transaction_id: UUID,
    line_raw_json: str,
    splits: tuple[ParsedSplit, ...],
) -> None:
    """Apply QIF tags to the newly-created transaction (#447).

    The line-level tags (L-line suffix + per-split union) land on
    ``transaction_tags`` via :class:`TransactionTagRepository`.
    The paired :func:`_apply_qif_posting_tags` writes the per-split
    subset to ``posting_tags`` so per-split attribution survives.
    """
    from tulip_storage.repositories import TransactionTagRepository
    from tulip_storage.repositories.transaction_tag import TagInvalidError

    line_tags = list(_extract_line_tags_from_raw_json(line_raw_json))
    seen: set[str] = set(line_tags)
    for split in splits:
        for tag in split.tags:
            if tag not in seen:
                seen.add(tag)
                line_tags.append(tag)
    if not line_tags:
        return
    try:
        TransactionTagRepository(session, household_id).set_tags(transaction_id, line_tags)
    except TagInvalidError:
        # Tag character-set is restrictive (#39); QIF tags that don't fit
        # (e.g. with spaces) are silently dropped rather than failing
        # the whole import. Operator can re-tag manually if needed.
        return


def _apply_qif_posting_tags(
    *,
    session: Session,
    household_id: UUID,
    splits: tuple[ParsedSplit, ...],
    split_posting_ids: list[UUID],
) -> None:
    """Apply each split's tags to its specific posting (#447 follow-up).

    Banktivity emits per-split tags via the ``S<category>/<tag>:<tag>``
    syntax — semantically those tags describe the split (one line item),
    not the parent transaction. The transaction-level write in
    :func:`_apply_qif_tags_from_raw_json` takes the union for cross-
    cutting reporting (``--tag walter`` finds every walter-touching
    transaction); this helper writes the per-split subset to
    ``posting_tags`` so per-posting attribution survives.

    Caller passes ``split_posting_ids`` in the same order as ``splits``.
    """
    from tulip_storage.repositories import PostingTagRepository
    from tulip_storage.repositories.transaction_tag import TagInvalidError

    if not splits or len(splits) != len(split_posting_ids):
        return
    repo = PostingTagRepository(session, household_id)
    for split, posting_id in zip(splits, split_posting_ids, strict=True):
        if not split.tags:
            continue
        try:
            repo.set_tags(posting_id, list(split.tags))
        except TagInvalidError:
            # Match the transaction-level policy: drop malformed silently.
            continue


async def apply_batch(
    *,
    session: Session,
    household_id: UUID,
    batch: ImportBatch,
    categorizer: Categorizer,
    actor_user_id: UUID | None,
    no_categorize: bool = False,
    as_posted: bool = False,
    treat_cleared_as_pending: bool = False,
) -> ApplyResult:
    """Promote every applicable line in ``batch``, then mark batch APPLIED.

    "Applicable" = not excluded and not already promoted. Excluded and
    already-promoted lines are silently skipped (counted in
    ``skipped_count``).

    ``as_posted=True`` (issue #210) lands every promoted line as
    ``POSTED`` instead of the default ``PENDING`` — bypassing the
    review step for migration workflows where the imported lines are
    already cleared by the source bank/tool.

    ``treat_cleared_as_pending=True`` (#279) forces every line back to
    PENDING even when the source format (QIF ``C`` field) marked it
    cleared or reconciled. The legacy behaviour for users who want a
    manual review pass.

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
            no_categorize=no_categorize,
            as_posted=as_posted,
            treat_cleared_as_pending=treat_cleared_as_pending,
        )
        transaction_ids.append(tx.id)

    ImportBatchRepository(session, household_id).mark_applied(batch.id)
    return ApplyResult(
        batch_id=batch.id,
        created_count=len(transaction_ids),
        skipped_count=skipped,
        transaction_ids=tuple(transaction_ids),
    )
