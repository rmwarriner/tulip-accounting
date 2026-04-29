"""Accounting engine implementation.

Two public operations:

- `post_transaction(tx, *, periods, allow_closed_period_override=False)` —
  the single chokepoint for committing a transaction to the ledger. Raises
  typed errors on invalid inputs.

- `balance_with_fx_postings(tx, *, fx_gain_loss_account_id, base_currency)` —
  helper that takes an unbalanced multi-currency transaction and adds
  offsetting postings to a designated FX gain/loss account so each currency
  sums to zero. The architecture (§5.6) calls for this to happen
  automatically on currency-crossing transactions; in this codebase it's a
  composable helper that callers (importers, the API layer) invoke before
  `post_transaction`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from tulip_core.money import Money
from tulip_core.transactions import Posting, Transaction, TransactionStatus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from tulip_core.periods import Period


class UnbalancedTransactionError(ValueError):
    """Raised when a transaction's postings do not sum to zero per currency."""


class ClosedPeriodError(ValueError):
    """Raised when a posting falls outside an open period.

    Three cases:
    - No period covers the transaction date.
    - A period covers the date but is soft-closed and override is not set.
    """


def post_transaction(
    tx: Transaction,
    *,
    periods: Iterable[Period],
    allow_closed_period_override: bool = False,
) -> Transaction:
    """Promote a balanced transaction to POSTED, after period validation.

    Already-posted transactions are returned unchanged (idempotent). Pending
    transactions must be balanced per currency; if so, a copy with status
    POSTED is returned.

    Period rules:
    - The transaction's date must fall inside some Period for the household.
      If not, ClosedPeriodError is raised with "no period" in the message.
    - If the matching Period is soft-closed, posting is rejected unless
      `allow_closed_period_override=True`.

    Args:
        tx: The transaction to post.
        periods: Candidate periods to search; only those whose
            household_id matches the transaction's are considered.
        allow_closed_period_override: When True, soft-closed periods accept
            postings (the API layer logs and audits the override).

    Returns:
        A Transaction with status POSTED. If the input was already POSTED,
        the same instance is returned.

    Raises:
        UnbalancedTransactionError: postings do not sum to zero per currency.
        ClosedPeriodError: no covering period, or period is soft-closed
            without override.

    """
    if tx.status is TransactionStatus.POSTED:
        return tx

    if not tx.is_balanced():
        raise UnbalancedTransactionError(
            f"transaction {tx.id} does not balance: {tx.balance_per_currency()}"
        )

    matching = [p for p in periods if p.household_id == tx.household_id and p.contains(tx.date)]
    if not matching:
        raise ClosedPeriodError(
            f"no period covers transaction date {tx.date} for household {tx.household_id}"
        )
    period = matching[0]
    if period.is_soft_closed and not allow_closed_period_override:
        raise ClosedPeriodError(
            f"period {period.id} is soft-closed; use allow_closed_period_override=True"
        )

    return replace(tx, status=TransactionStatus.POSTED)


def balance_with_fx_postings(
    tx: Transaction,
    *,
    fx_gain_loss_account_id: UUID,
    base_currency: str,  # reserved for future allocation logic
) -> Transaction:
    """Return a Transaction balanced per currency by adding FX postings.

    For each currency whose postings have a non-zero net balance, this
    appends an offsetting posting in that same currency to the designated
    FX gain/loss account. The result balances per currency by construction.

    Already-balanced transactions are returned unchanged.

    Note:
        `base_currency` is accepted for forward-compatibility — when the
        engine learns to translate FX postings to the household base
        currency for reporting purposes, callers won't need to change. For
        now, postings are emitted in the imbalanced currencies directly.

    """
    del base_currency  # unused in v1; see docstring

    if tx.is_balanced():
        return tx

    new_postings: list[Posting] = list(tx.postings)
    for currency, balance in tx.balance_per_currency().items():
        if balance == 0:
            continue
        # Offsetting posting: same currency, negated balance, to fx_acct.
        new_postings.append(
            Posting(
                id=uuid4(),
                account_id=fx_gain_loss_account_id,
                amount=Money(-balance, currency),
                memo="FX gain/loss balancing",
            )
        )
    return replace(tx, postings=tuple(new_postings))
