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
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from tulip_core.account import AccountType
from tulip_core.allocation.shadow_posting import ShadowPosting
from tulip_core.allocation.shadow_transaction import (
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
)
from tulip_core.money import Money

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from uuid import UUID

    from tulip_core.allocation.pool import Pool
    from tulip_core.transactions import Transaction


class UnbalancedShadowTransactionError(ValueError):
    """Raised when a shadow transaction's postings don't sum to zero per currency."""


class UnknownPoolError(ValueError):
    """Raised when a posting references a pool that doesn't exist in the household."""


class PoolCurrencyMismatchError(ValueError):
    """Raised when a posting's currency doesn't match its pool's currency."""


class InactivePoolError(ValueError):
    """Raised when a posting writes to a deactivated pool."""


class InvalidAccountTypePairingError(ValueError):
    """Raised when a pool-tagged main posting is on an account whose type forbids pairing.

    v1 permits pool-tagging on EXPENSE accounts only. The account-type
    table can be widened in a later slice if a real use case lands; for
    now anything else is rejected so the auto-pairing semantics stay
    pinned to the worked examples in ADR-0001.
    """


class MultiCurrencyPoolTaggingError(ValueError):
    """Raised when pool-tagged postings on a single main tx span multiple currencies.

    Multi-currency main transactions are legal in general (FX flows), but
    the absorbing-leg sign rule and the per-currency balance interaction
    haven't been worked out for cross-currency shadow pairing in v1.
    """


class UnsupportedRefundShapedShadowTxError(ValueError):
    """Raised when the inferred shadow tx would have a non-negative net pool effect.

    "Spending-shaped" main transactions produce a negative net pool effect
    (money out of envelopes, absorbed by Spent). The mirror case — money
    into envelopes via a refund-shaped main tx — needs an Inflow-side
    absorbing leg with reason ``REFUND``, which isn't in the
    ``ShadowTxReason`` enum yet. Out of scope for v1; deferred to an ADR
    amendment.
    """


# v1 sign rule: only EXPENSE accounts may carry pool_id. Other types
# can be added when a real use case lands; until then anything else is
# rejected at this layer (and at the API pre-flight check too).
_PAIRABLE_ACCOUNT_TYPES: frozenset[AccountType] = frozenset({AccountType.EXPENSE})

# For an EXPENSE leg with positive amount (the spending side of a
# debit-shaped expense posting), the shadow leg's amount is
# `-sign * main_amount = -1 * +amount = -amount` — money out of envelope.
# Documented as the worked example in ADR-0001 §B.
_SIGN_FOR_ACCOUNT_TYPE: dict[AccountType, int] = {
    AccountType.EXPENSE: 1,
}


def derive_paired_shadow_tx(
    main_tx: Transaction,
    *,
    account_types_by_id: Mapping[UUID, AccountType],
    spent_pool_by_currency: Mapping[str, UUID],
) -> ShadowTransaction | None:
    """Build the shadow tx paired to ``main_tx`` when its postings carry ``pool_id``.

    Returns ``None`` when no posting carries ``pool_id`` — caller skips the
    shadow-ledger write entirely. Otherwise returns a POSTED shadow tx
    with one leg per pool-tagged main posting plus an absorbing leg in
    the household's ``Spent`` system pool of the appropriate currency.
    Per ADR-0001's pairing rule: one main tx → at most one paired shadow
    tx; multi-pool effects are bundled as additional legs.

    Args:
        main_tx: The already-balanced main-ledger transaction.
        account_types_by_id: Resolver from ``account_id`` to ``AccountType``.
            The router builds this once from the AccountRepository before
            calling.
        spent_pool_by_currency: Resolver from currency to the household's
            ``Spent`` system-pool UUID. The router obtains it via
            ``AllocationPoolRepository.get_or_create_system_pools`` and
            extracts the ``PoolType.SPENT`` entry.

    Returns:
        A POSTED ShadowTransaction with ``paired_main_tx_id`` set and
        ``description`` suffixed with " (envelope effects)". The shadow
        tx's per-currency sums are zero by construction.

    Raises:
        InvalidAccountTypePairingError: a pool-tagged posting is on an
            account whose type is not EXPENSE.
        MultiCurrencyPoolTaggingError: pool-tagged postings span more
            than one currency on this main tx.
        UnsupportedRefundShapedShadowTxError: the net pool effect would
            be non-negative (refund-shaped); v1 only supports negative
            (spending-shaped) effects.

    """
    pool_tagged = [p for p in main_tx.postings if p.pool_id is not None]
    if not pool_tagged:
        return None

    currencies = {p.amount.currency for p in pool_tagged}
    if len(currencies) > 1:
        raise MultiCurrencyPoolTaggingError(
            f"pool-tagged postings span multiple currencies "
            f"({sorted(currencies)}) on main tx {main_tx.id}; "
            "rejected in v1 — see ADR-0001"
        )
    currency = next(iter(currencies))

    for p in pool_tagged:
        atype = account_types_by_id.get(p.account_id)
        if atype is None or atype not in _PAIRABLE_ACCOUNT_TYPES:
            raise InvalidAccountTypePairingError(
                f"posting {p.id} is on account {p.account_id} of type "
                f"{atype.value if atype else 'unknown'!r}; only EXPENSE "
                "accounts may carry pool_id in v1"
            )

    shadow_postings: list[ShadowPosting] = []
    net_pool_effect = Decimal(0)
    for p in pool_tagged:
        # mypy knows account_types_by_id.get is Optional; the loop above
        # already raised on None, so a non-None lookup is safe here.
        atype = account_types_by_id[p.account_id]
        sign = _SIGN_FOR_ACCOUNT_TYPE[atype]
        shadow_amount = -sign * p.amount.amount
        net_pool_effect += shadow_amount
        # mypy: pool_id is Optional[UUID] on Posting but we filtered to
        # non-None above; assert via cast.
        assert p.pool_id is not None  # noqa: S101 - filtered above
        shadow_postings.append(
            ShadowPosting(
                id=uuid4(),
                pool_id=p.pool_id,
                amount=Money(shadow_amount, currency),
            )
        )

    if net_pool_effect >= 0:
        # Net positive (or zero) shadow effect = money flowing INTO
        # envelopes. Refund-shaped pairing requires an Inflow absorbing
        # leg + a REFUND reason that isn't in ShadowTxReason yet.
        raise UnsupportedRefundShapedShadowTxError(
            f"main tx {main_tx.id} has net pool effect "
            f"{net_pool_effect} {currency}; v1 only supports "
            "spending-shaped (negative net effect) auto-pairing"
        )

    spent_pool_id = spent_pool_by_currency.get(currency)
    if spent_pool_id is None:
        # Caller is responsible for ensuring system pools exist for
        # every pool-tagged currency. Reaching this branch is a Tulip
        # bug, not a user error.
        raise ValueError(
            f"no Spent system pool registered for currency {currency!r} "
            "— caller must materialize system pools before pairing"
        )

    shadow_postings.append(
        ShadowPosting(
            id=uuid4(),
            pool_id=spent_pool_id,
            amount=Money(-net_pool_effect, currency),
        )
    )

    return ShadowTransaction(
        id=uuid4(),
        household_id=main_tx.household_id,
        date=main_tx.date,
        description=f"{main_tx.description} (envelope effects)",
        reason=ShadowTxReason.SPEND,
        postings=tuple(shadow_postings),
        status=ShadowTxStatus.POSTED,
        paired_main_tx_id=main_tx.id,
        created_by_user_id=main_tx.created_by_user_id,
    )


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
