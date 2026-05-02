"""Tests for tulip_core.allocation.engine.post_shadow_transaction."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tulip_core.allocation import (
    InactivePoolError,
    Pool,
    PoolCurrencyMismatchError,
    PoolType,
    ShadowPosting,
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
    UnbalancedShadowTransactionError,
    UnknownPoolError,
    post_shadow_transaction,
)
from tulip_core.money import Money


def _pool(
    *,
    pool_id: UUID,
    household_id: UUID,
    currency: str = "USD",
    pool_type: PoolType = PoolType.ENVELOPE,
    is_system: bool = False,
    is_active: bool = True,
) -> Pool:
    return Pool(
        id=pool_id,
        household_id=household_id,
        pool_type=pool_type,
        name=f"pool-{pool_id}",
        currency=currency,
        is_system=is_system,
        is_active=is_active,
    )


def _build_tx(
    *,
    household_id: UUID,
    postings: tuple[ShadowPosting, ...],
    status: ShadowTxStatus = ShadowTxStatus.PENDING,
) -> ShadowTransaction:
    return ShadowTransaction(
        id=uuid4(),
        household_id=household_id,
        date=date(2026, 6, 1),
        description="x",
        reason=ShadowTxReason.REFILL,
        postings=postings,
        status=status,
    )


def test_post_promotes_pending_to_posted() -> None:
    household_id = uuid4()
    src = uuid4()
    dst = uuid4()
    pools = [
        _pool(
            pool_id=src,
            household_id=household_id,
            pool_type=PoolType.UNALLOCATED,
            is_system=True,
        ),
        _pool(pool_id=dst, household_id=household_id),
    ]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=src, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=dst, amount=Money(Decimal("250"), "USD")),
        ),
    )
    posted = post_shadow_transaction(tx, pools=pools)
    assert posted.status is ShadowTxStatus.POSTED


def test_idempotent_for_already_posted() -> None:
    household_id = uuid4()
    src = uuid4()
    dst = uuid4()
    pools = [
        _pool(
            pool_id=src,
            household_id=household_id,
            pool_type=PoolType.UNALLOCATED,
            is_system=True,
        ),
        _pool(pool_id=dst, household_id=household_id),
    ]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=src, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=dst, amount=Money(Decimal("250"), "USD")),
        ),
        status=ShadowTxStatus.POSTED,
    )
    again = post_shadow_transaction(tx, pools=pools)
    assert again is tx


def test_voided_cannot_be_re_posted() -> None:
    household_id = uuid4()
    src = uuid4()
    dst = uuid4()
    pools = [
        _pool(
            pool_id=src,
            household_id=household_id,
            pool_type=PoolType.UNALLOCATED,
            is_system=True,
        ),
        _pool(pool_id=dst, household_id=household_id),
    ]
    # Build via PENDING then mutate status via dataclass replace would require
    # the engine; for the test we construct directly with VOIDED.
    # ShadowTransaction.__post_init__ skips balance check for non-POSTED status.
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=src, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=dst, amount=Money(Decimal("250"), "USD")),
        ),
        status=ShadowTxStatus.VOIDED,
    )
    with pytest.raises(ValueError, match="voided"):
        post_shadow_transaction(tx, pools=pools)


def test_unknown_pool_rejected() -> None:
    household_id = uuid4()
    known = uuid4()
    unknown = uuid4()
    pools = [_pool(pool_id=known, household_id=household_id)]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=known, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=unknown, amount=Money(Decimal("250"), "USD")),
        ),
    )
    with pytest.raises(UnknownPoolError):
        post_shadow_transaction(tx, pools=pools)


def test_pool_in_other_household_rejected() -> None:
    household_id = uuid4()
    other_household = uuid4()
    p1 = uuid4()
    p2 = uuid4()
    pools = [
        _pool(pool_id=p1, household_id=household_id),
        _pool(pool_id=p2, household_id=other_household),
    ]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=p1, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=p2, amount=Money(Decimal("250"), "USD")),
        ),
    )
    with pytest.raises(UnknownPoolError):
        post_shadow_transaction(tx, pools=pools)


def test_inactive_pool_rejected() -> None:
    household_id = uuid4()
    src = uuid4()
    dst = uuid4()
    pools = [
        _pool(
            pool_id=src,
            household_id=household_id,
            pool_type=PoolType.UNALLOCATED,
            is_system=True,
        ),
        _pool(pool_id=dst, household_id=household_id, is_active=False),
    ]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=src, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=dst, amount=Money(Decimal("250"), "USD")),
        ),
    )
    with pytest.raises(InactivePoolError):
        post_shadow_transaction(tx, pools=pools)


def test_pool_currency_mismatch_rejected() -> None:
    household_id = uuid4()
    src = uuid4()
    dst = uuid4()
    pools = [
        _pool(
            pool_id=src,
            household_id=household_id,
            currency="USD",
            pool_type=PoolType.UNALLOCATED,
            is_system=True,
        ),
        _pool(pool_id=dst, household_id=household_id, currency="USD"),
    ]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=src, amount=Money(Decimal("-250"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=dst, amount=Money(Decimal("250"), "EUR")),
        ),
    )
    # The transaction's per-currency sum is also unbalanced (USD: -250, EUR: 250),
    # but the currency-mismatch check fires first.
    with pytest.raises(PoolCurrencyMismatchError):
        post_shadow_transaction(tx, pools=pools)


def test_unbalanced_rejected() -> None:
    # Constructed as PENDING (which permits imbalance) and then handed to the
    # engine. The engine then rejects the imbalance.
    household_id = uuid4()
    src = uuid4()
    dst = uuid4()
    pools = [
        _pool(
            pool_id=src,
            household_id=household_id,
            pool_type=PoolType.UNALLOCATED,
            is_system=True,
        ),
        _pool(pool_id=dst, household_id=household_id),
    ]
    tx = _build_tx(
        household_id=household_id,
        postings=(
            ShadowPosting(id=uuid4(), pool_id=src, amount=Money(Decimal("-100"), "USD")),
            ShadowPosting(id=uuid4(), pool_id=dst, amount=Money(Decimal("250"), "USD")),
        ),
    )
    with pytest.raises(UnbalancedShadowTransactionError):
        post_shadow_transaction(tx, pools=pools)
