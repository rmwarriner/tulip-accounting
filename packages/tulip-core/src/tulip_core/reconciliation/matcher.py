"""Reconciliation matcher: bank statement lines → candidate ledger matches.

Per ADR-0004 §Q1-Q2. The matcher is a pure function: given a sequence of
``StatementLine`` and a sequence of ledger ``Transaction``, plus the
target ``account_id``, it returns ``CandidateMatch`` rows for each
``(line, tx)`` pair that satisfies the candidacy rules:

1. The transaction has a posting on ``account_id`` (matcher only inspects
   the bank-side of multi-posting txs).
2. The amount on that posting equals ``line.amount`` exactly (per
   currency — ``Money.__eq__`` enforces both).
3. The transaction's date is within ``MATCH_DATE_WINDOW`` of the line's
   ``posted_date``.
4. The transaction is not already reconciled (caller passes
   ``reconciled_transaction_ids``; the matcher has no DB access).
5. The transaction is in a ledger status (``POSTED`` or ``RECONCILED``);
   ``PENDING`` is workflow state and is not eligible.

Each candidate is bucketed into ``HIGH``/``MEDIUM``/``LOW`` per ADR §Q2:

- ``HIGH``: same date (±0 days) AND fuzzy ≥ ``FUZZY_HIGH_THRESHOLD``.
- ``MEDIUM``: same date with lower fuzzy, OR within ±3 days with fuzzy ≥
  ``FUZZY_MEDIUM_THRESHOLD``. Note: same-date is *never* LOW.
- ``LOW``: ±1-3 days with fuzzy < ``FUZZY_MEDIUM_THRESHOLD``.

Output is sorted by ``(statement_line_id, ledger_transaction_id)`` for
deterministic test diffs.

Caller obligations:

- Pre-filter ``ledger_transactions`` to a sensible date window. The
  matcher walks the list linearly; passing a 10-year ledger is wasteful
  but not incorrect.
- Populate ``reconciled_transaction_ids`` from the source-of-truth
  storage query (``transactions.reconciliation_id IS NOT NULL``).
  Forgetting this argument allows double-matches in production.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from rapidfuzz import fuzz, utils

from tulip_core.reconciliation.candidate_match import CandidateMatch
from tulip_core.reconciliation.match_confidence import MatchConfidence
from tulip_core.transactions import TransactionStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from tulip_core.reconciliation.statement_line import StatementLine
    from tulip_core.transactions import Posting, Transaction

#: Per ADR-0004 §Q1: candidate matches accept date drift within this window.
#: A private constant — not user-tunable in v1. Bumping requires both an
#: ADR amendment and updating the boundary tests.
MATCH_DATE_WINDOW: Final[timedelta] = timedelta(days=3)

#: Per ADR-0004 §Q2: HIGH bucket needs ``token_set_ratio >= 0.9`` (after
#: divide-by-100). Inclusive on the boundary.
FUZZY_HIGH_THRESHOLD: Final[float] = 0.9

#: Per ADR-0004 §Q2: MEDIUM bucket (with date drift) needs ``>= 0.6``.
#: Below this with date drift is LOW; same-date with low fuzzy stays MEDIUM.
FUZZY_MEDIUM_THRESHOLD: Final[float] = 0.6


def _describe_similarity(a: str, b: str) -> float:
    """Return ``token_set_ratio(a, b) / 100`` after lowercase + punctuation strip.

    rapidfuzz's ``token_set_ratio`` is sensitive to case and punctuation
    by default. ``utils.default_process`` lowercases and strips punctuation
    before tokenizing — that's the behavior we want, since banks emit
    statement descriptions in mixed case ("PAYPAL *AMAZON, INC.") that
    the user types as ("Amazon").

    Returned score is clamped to ``[0.0, 1.0]``.
    """
    score = fuzz.token_set_ratio(a, b, processor=utils.default_process)
    return max(0.0, min(1.0, score / 100.0))


def _classify_confidence(date_delta_days: int, fuzzy_score: float) -> MatchConfidence:
    """Classify a candidate per ADR-0004 §Q2."""
    if date_delta_days == 0:
        if fuzzy_score >= FUZZY_HIGH_THRESHOLD:
            return MatchConfidence.HIGH
        # Same-date is never LOW per the ADR — even no-fuzzy matches
        # surface for user confirmation.
        return MatchConfidence.MEDIUM
    # date_delta_days in 1..MATCH_DATE_WINDOW.days
    if fuzzy_score >= FUZZY_MEDIUM_THRESHOLD:
        return MatchConfidence.MEDIUM
    return MatchConfidence.LOW


def find_candidates(
    statement_lines: Sequence[StatementLine],
    ledger_transactions: Sequence[Transaction],
    *,
    account_id: UUID,
    reconciled_transaction_ids: frozenset[UUID] = frozenset(),
) -> list[CandidateMatch]:
    """Emit ``CandidateMatch`` rows for each ``(line, tx)`` pair that meets §Q1.

    Args:
        statement_lines: Bank-side rows.
        ledger_transactions: Ledger-side transactions, pre-filtered by the
            caller to a sensible date range.
        account_id: The bank account being reconciled. The matcher only
            inspects postings on this account.
        reconciled_transaction_ids: IDs already linked to a previous
            reconciliation; excluded from candidates. Caller queries the
            ``transactions.reconciliation_id`` denormalization to populate.

    Returns:
        Stable-ordered list (by ``(statement_line_id, ledger_transaction_id)``);
        no duplicates by id-pair.

    """
    # Pre-build (tx, posting) tuples for txs eligible at the
    # ledger-status + account-scope + reconciled-set level. Computed once
    # per query rather than per (line, tx) pair.
    eligible: list[tuple[Transaction, Posting]] = []
    for tx in ledger_transactions:
        if tx.status not in (TransactionStatus.POSTED, TransactionStatus.RECONCILED):
            continue
        if tx.id in reconciled_transaction_ids:
            continue
        for posting in tx.postings:
            if posting.account_id == account_id:
                eligible.append((tx, posting))
                # Multi-posting on the same account is rare; we stop at
                # the first matching posting per tx and emit one
                # candidate per tx (split-match is P5.4's job).
                break

    out: list[CandidateMatch] = []
    for line in statement_lines:
        for tx, posting in eligible:
            if posting.amount != line.amount:
                continue
            delta = line.posted_date - tx.date
            delta_days = abs(delta.days)
            if delta_days > MATCH_DATE_WINDOW.days:
                continue
            fuzzy = _describe_similarity(line.description, tx.description)
            confidence = _classify_confidence(delta_days, fuzzy)
            out.append(
                CandidateMatch(
                    statement_line_id=line.id,
                    ledger_transaction_id=tx.id,
                    match_amount=line.amount,
                    confidence=confidence,
                    fuzzy_score=fuzzy,
                )
            )

    out.sort(key=lambda c: (c.statement_line_id, c.ledger_transaction_id))
    return out
