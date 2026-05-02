"""Tests for AllocationPoolRepository and ShadowTransactionRepository."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_core.allocation import (
    ShadowPosting as DomainShadowPosting,
)
from tulip_core.allocation import (
    ShadowTransaction as DomainShadowTransaction,
)
from tulip_core.allocation import (
    ShadowTxReason as DomainShadowTxReason,
)
from tulip_core.allocation import (
    ShadowTxStatus as DomainShadowTxStatus,
)
from tulip_core.money import Money
from tulip_storage.models import (
    AllocationPool,
    Household,
    PoolType,
    ShadowTxStatus,
)
from tulip_storage.repositories import (
    AllocationPoolRepository,
    ShadowTransactionRepository,
)


def _seed_household(session: Session, *, base_currency: str = "USD") -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency=base_currency)
    session.add(h)
    session.commit()
    return h


# ---- AllocationPoolRepository ------------------------------------------


class TestPoolCreate:
    def test_create_envelope(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        p = repo.create(pool_type=PoolType.ENVELOPE, name="Groceries", currency="USD")
        session.commit()
        assert p.is_system is False
        assert p.is_active is True

    def test_create_system_pool_idempotent(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        first = repo.get_or_create_system_pools(currency="USD")
        session.commit()
        second = repo.get_or_create_system_pools(currency="USD")
        session.commit()
        assert {p.id for p in first.values()} == {p.id for p in second.values()}
        assert set(first.keys()) == {PoolType.INFLOW, PoolType.UNALLOCATED, PoolType.SPENT}

    def test_system_pools_are_per_currency(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        usd = repo.get_or_create_system_pools(currency="USD")
        eur = repo.get_or_create_system_pools(currency="EUR")
        session.commit()
        # Six pool rows total (3 system types x 2 currencies).
        assert {p.id for p in usd.values()}.isdisjoint({p.id for p in eur.values()})

    def test_get_system_pool_rejects_non_system_type(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        with pytest.raises(ValueError, match="not a system pool type"):
            repo.get_system_pool(pool_type=PoolType.ENVELOPE, currency="USD")


class TestPoolListAndDeactivate:
    def test_list_active_includes_system(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        repo.get_or_create_system_pools(currency="USD")
        repo.create(pool_type=PoolType.ENVELOPE, name="Groceries", currency="USD")
        session.commit()
        pools = repo.list_active()
        assert len(pools) == 4
        assert sum(1 for p in pools if p.is_system) == 3

    def test_deactivate_user_pool(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        p = repo.create(pool_type=PoolType.ENVELOPE, name="Groceries", currency="USD")
        session.commit()
        repo.deactivate(p.id)
        session.commit()
        again = repo.get(p.id)
        assert again is not None
        assert again.is_active is False

    def test_deactivate_system_pool_rejected(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        pools = repo.get_or_create_system_pools(currency="USD")
        session.commit()
        with pytest.raises(ValueError, match="system pool"):
            repo.deactivate(pools[PoolType.UNALLOCATED].id)

    def test_deactivate_missing_raises(self, session: Session) -> None:
        h = _seed_household(session)
        repo = AllocationPoolRepository(session, h.id)
        with pytest.raises(LookupError):
            repo.deactivate(uuid4())


# ---- ShadowTransactionRepository ---------------------------------------


def _build_balanced_domain_tx(
    *,
    household_id: UUID,
    src_pool_id: UUID,
    dst_pool_id: UUID,
    amount: Decimal = Decimal("250"),
    currency: str = "USD",
    tx_date: date = date(2026, 6, 1),
    paired_main_tx_id: UUID | None = None,
) -> DomainShadowTransaction:
    return DomainShadowTransaction(
        id=uuid4(),
        household_id=household_id,
        date=tx_date,
        description="Refill Groceries",
        reason=DomainShadowTxReason.REFILL,
        postings=(
            DomainShadowPosting(id=uuid4(), pool_id=src_pool_id, amount=Money(-amount, currency)),
            DomainShadowPosting(id=uuid4(), pool_id=dst_pool_id, amount=Money(amount, currency)),
        ),
        status=DomainShadowTxStatus.POSTED,
        paired_main_tx_id=paired_main_tx_id,
    )


def _seed_pools_for_balance_test(
    session: Session, household: Household
) -> tuple[AllocationPool, AllocationPool]:
    pool_repo = AllocationPoolRepository(session, household.id)
    sys_pools = pool_repo.get_or_create_system_pools(currency="USD")
    src = sys_pools[PoolType.UNALLOCATED]
    dst = pool_repo.create(pool_type=PoolType.ENVELOPE, name="Groceries", currency="USD")
    session.commit()
    return src, dst


class TestSavePostedShadowTx:
    def test_save_balanced(self, session: Session) -> None:
        h = _seed_household(session)
        src, dst = _seed_pools_for_balance_test(session, h)
        stx_repo = ShadowTransactionRepository(session, h.id)
        domain_tx = _build_balanced_domain_tx(
            household_id=h.id, src_pool_id=src.id, dst_pool_id=dst.id
        )
        stx = stx_repo.save_balanced(domain_tx)
        session.commit()
        assert stx.status is ShadowTxStatus.POSTED
        assert stx.posted_at is not None
        postings = stx_repo.list_postings(stx.id)
        assert len(postings) == 2

    def test_balance_for_pool_after_refill(self, session: Session) -> None:
        h = _seed_household(session)
        src, dst = _seed_pools_for_balance_test(session, h)
        stx_repo = ShadowTransactionRepository(session, h.id)
        domain_tx = _build_balanced_domain_tx(
            household_id=h.id, src_pool_id=src.id, dst_pool_id=dst.id, amount=Decimal("250")
        )
        stx_repo.save_balanced(domain_tx)
        session.commit()
        assert stx_repo.balance_for_pool(dst.id) == {"USD": Decimal("250")}
        assert stx_repo.balance_for_pool(src.id) == {"USD": Decimal("-250")}

    def test_balance_for_empty_pool(self, session: Session) -> None:
        h = _seed_household(session)
        _, dst = _seed_pools_for_balance_test(session, h)
        stx_repo = ShadowTransactionRepository(session, h.id)
        # No shadow tx posted to dst yet.
        assert stx_repo.balance_for_pool(dst.id) == {}

    def test_balance_for_pool_currency_filter(self, session: Session) -> None:
        h = _seed_household(session)
        src, dst = _seed_pools_for_balance_test(session, h)
        stx_repo = ShadowTransactionRepository(session, h.id)
        stx_repo.save_balanced(
            _build_balanced_domain_tx(
                household_id=h.id, src_pool_id=src.id, dst_pool_id=dst.id, amount=Decimal("250")
            )
        )
        session.commit()
        # Filter to a currency the pool has no postings in.
        assert stx_repo.balance_for_pool(dst.id, currency="EUR") == {}
        assert stx_repo.balance_for_pool(dst.id, currency="USD") == {"USD": Decimal("250")}

    def test_balance_for_pool_as_of(self, session: Session) -> None:
        h = _seed_household(session)
        src, dst = _seed_pools_for_balance_test(session, h)
        stx_repo = ShadowTransactionRepository(session, h.id)
        # Two refills on different dates.
        stx_repo.save_balanced(
            _build_balanced_domain_tx(
                household_id=h.id,
                src_pool_id=src.id,
                dst_pool_id=dst.id,
                amount=Decimal("100"),
                tx_date=date(2026, 6, 1),
            )
        )
        stx_repo.save_balanced(
            _build_balanced_domain_tx(
                household_id=h.id,
                src_pool_id=src.id,
                dst_pool_id=dst.id,
                amount=Decimal("250"),
                tx_date=date(2026, 7, 1),
            )
        )
        session.commit()
        # As-of June 30 — only the first refill counts.
        assert stx_repo.balance_for_pool(dst.id, as_of=date(2026, 6, 30)) == {"USD": Decimal("100")}
        # As-of July 31 — both refills count.
        assert stx_repo.balance_for_pool(dst.id, as_of=date(2026, 7, 31)) == {"USD": Decimal("350")}


class TestPendingShadowTxExcludedFromBalance:
    def test_pending_does_not_contribute(self, session: Session) -> None:
        h = _seed_household(session)
        src, dst = _seed_pools_for_balance_test(session, h)
        stx_repo = ShadowTransactionRepository(session, h.id)
        # Build a domain tx in PENDING status (which is permitted to exist
        # even unbalanced); save it and check it doesn't affect balance.
        domain_tx = DomainShadowTransaction(
            id=uuid4(),
            household_id=h.id,
            date=date(2026, 6, 1),
            description="WIP",
            reason=DomainShadowTxReason.MANUAL,
            postings=(
                DomainShadowPosting(
                    id=uuid4(), pool_id=src.id, amount=Money(Decimal("-100"), "USD")
                ),
                DomainShadowPosting(
                    id=uuid4(), pool_id=dst.id, amount=Money(Decimal("100"), "USD")
                ),
            ),
            status=DomainShadowTxStatus.PENDING,
        )
        stx_repo.save_balanced(domain_tx)
        session.commit()
        # Pending shouldn't show up in derived balances.
        assert stx_repo.balance_for_pool(dst.id) == {}
