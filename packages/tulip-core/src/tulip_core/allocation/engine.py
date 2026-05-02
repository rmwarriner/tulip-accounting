"""Shadow-ledger engine.

Single public operation: :func:`post_shadow_transaction`. Validates the
balance invariant, the per-pool currency match, and pool activeness, and
returns a copy of the transaction with status ``POSTED``.

Period validation is deliberately out of scope for v1 — shadow transactions
record *intent* (refills, transfers, rollovers) rather than real money
movement, and the period gate is enforced where the main-ledger transaction
that triggered the shadow tx lives. User-initiated shadow transactions can
have whatever date the user picks; the period rules around them will land
in P4.1 alongside the API surface.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from tulip_core.allocation.shadow_transaction import ShadowTransaction, ShadowTxStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tulip_core.allocation.pool import Pool


class UnbalancedShadowTransactionError(ValueError):
    """Raised when a shadow transaction's postings don't sum to zero per currency."""


class UnknownPoolError(ValueError):
    """Raised when a posting references a pool that doesn't exist in the household."""


class PoolCurrencyMismatchError(ValueError):
    """Raised when a posting's currency doesn't match its pool's currency."""


class InactivePoolError(ValueError):
    """Raised when a posting writes to a deactivated pool."""


def post_shadow_transaction(
    tx: ShadowTransaction,
    *,
    pools: Iterable[Pool],
) -> ShadowTransaction:
    """Promote a shadow transaction to POSTED after validation.

    Already-posted transactions are returned unchanged (idempotent). Voided
    transactions cannot be re-posted and raise ``ValueError``.

    Validation order:

    1. Every posting's ``pool_id`` resolves to a pool in the same household.
    2. Every referenced pool is active.
    3. Every posting's currency matches its pool's currency.
    4. Per-currency sums are zero.

    Args:
        tx: The shadow transaction to post.
        pools: Candidate pools to validate against; only those whose
            ``household_id`` matches the transaction's are considered.

    Returns:
        A ShadowTransaction with status POSTED. If the input was already
        POSTED, the same instance is returned.

    Raises:
        UnknownPoolError: a posting's pool_id is not in the candidate set.
        InactivePoolError: a referenced pool is deactivated.
        PoolCurrencyMismatchError: a posting's currency differs from its pool's.
        UnbalancedShadowTransactionError: postings don't sum to zero per currency.
        ValueError: the transaction is already VOIDED.

    """
    if tx.status is ShadowTxStatus.POSTED:
        return tx
    if tx.status is ShadowTxStatus.VOIDED:
        raise ValueError(f"cannot post voided shadow transaction {tx.id}")

    pools_by_id = {p.id: p for p in pools if p.household_id == tx.household_id}

    for posting in tx.postings:
        pool = pools_by_id.get(posting.pool_id)
        if pool is None:
            raise UnknownPoolError(
                f"posting {posting.id} references unknown pool {posting.pool_id} "
                f"in household {tx.household_id}"
            )
        if not pool.is_active:
            raise InactivePoolError(
                f"posting {posting.id} writes to inactive pool {pool.id} ({pool.name!r})"
            )
        if pool.currency != posting.amount.currency:
            raise PoolCurrencyMismatchError(
                f"posting {posting.id} currency {posting.amount.currency!r} "
                f"does not match pool {pool.id} currency {pool.currency!r}"
            )

    if not tx.is_balanced():
        raise UnbalancedShadowTransactionError(
            f"shadow transaction {tx.id} does not balance: {tx.balance_per_currency()}"
        )

    return replace(tx, status=ShadowTxStatus.POSTED)
