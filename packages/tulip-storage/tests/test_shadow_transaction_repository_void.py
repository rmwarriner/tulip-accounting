"""Tests for the ShadowTransactionRepository.void chokepoint (P5.0)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_core.allocation import (
    ShadowPosting as DomainShadowPosting,
)
from tulip_core.allocation import (
    ShadowTransaction as DomainShadowTx,
)
from tulip_core.allocation import (
    ShadowTxReason,
    ShadowTxStatus,
)
from tulip_core.money import Money
from tulip_storage.models import (
    AllocationPool,
    Household,
    PoolType,
)
from tulip_storage.models import (
    ShadowTxStatus as StorageShadowStatus,
)
from tulip_storage.repositories import ShadowTransactionRepository
from tulip_storage.repositories.shadow_transaction import ShadowTxNotVoidableError


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _seed_pools(session: Session, household: Household) -> tuple[AllocationPool, AllocationPool]:
    """Create one user envelope + one Spent system pool, both USD."""
    env = AllocationPool(
        household_id=household.id,
        id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
        is_active=True,
        is_system=False,
    )
    spent = AllocationPool(
        household_id=household.id,
        id=uuid4(),
        pool_type=PoolType.SPENT,
        name="Spent USD",
        currency="USD",
        is_active=True,
        is_system=True,
    )
    session.add_all([env, spent])
    session.commit()
    return env, spent


def _post_shadow_tx(
    session: Session,
    household: Household,
    *,
    env: AllocationPool,
    spent: AllocationPool,
) -> DomainShadowTx:
    """Insert a POSTED shadow tx via save_balanced."""
    domain_tx = DomainShadowTx(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 6, 1),
        description="Groceries spend pairing",
        reason=ShadowTxReason.SPEND,
        status=ShadowTxStatus.POSTED,
        postings=(
            DomainShadowPosting(
                id=uuid4(),
                pool_id=env.id,
                amount=Money(Decimal("-25.00"), "USD"),
            ),
            DomainShadowPosting(
                id=uuid4(),
                pool_id=spent.id,
                amount=Money(Decimal("25.00"), "USD"),
            ),
        ),
    )
    ShadowTransactionRepository(session, household.id).save_balanced(domain_tx)
    session.commit()
    return domain_tx


class TestVoid:
    def test_posted_shadow_tx_status_flips_to_voided(self, session: Session, household: Household):
        env, spent = _seed_pools(session, household)
        stx = _post_shadow_tx(session, household, env=env, spent=spent)

        repo = ShadowTransactionRepository(session, household.id)
        voided_at = datetime.now(tz=UTC)
        repo.void(stx.id, voided_at=voided_at)
        session.commit()

        loaded = repo.get(stx.id)
        assert loaded is not None
        assert loaded.status is StorageShadowStatus.VOIDED
        assert loaded.voided_at is not None

    def test_balance_for_pool_excludes_voided_shadow_tx(
        self, session: Session, household: Household
    ):
        # Before void: pool balance reflects the spend.
        # After void: pool balance is zero (status=voided is excluded).
        env, spent = _seed_pools(session, household)
        stx = _post_shadow_tx(session, household, env=env, spent=spent)

        repo = ShadowTransactionRepository(session, household.id)
        before = repo.balance_for_pool(env.id, currency="USD")
        assert before["USD"] == Decimal("-25.00")

        repo.void(stx.id, voided_at=datetime.now(tz=UTC))
        session.commit()

        after = repo.balance_for_pool(env.id, currency="USD")
        assert after.get("USD", Decimal("0")) == Decimal("0")

    def test_already_voided_is_idempotent_noop(self, session: Session, household: Household):
        env, spent = _seed_pools(session, household)
        stx = _post_shadow_tx(session, household, env=env, spent=spent)

        repo = ShadowTransactionRepository(session, household.id)
        first_at = datetime.now(tz=UTC)
        repo.void(stx.id, voided_at=first_at)
        session.commit()
        first_loaded_at = repo.get(stx.id).voided_at  # type: ignore[union-attr]

        # Second void should be a no-op — voided_at unchanged.
        later = datetime.now(tz=UTC)
        repo.void(stx.id, voided_at=later)
        session.commit()
        second_loaded_at = repo.get(stx.id).voided_at  # type: ignore[union-attr]
        assert first_loaded_at == second_loaded_at

    def test_pending_shadow_tx_rejected(self, session: Session, household: Household):
        _seed_pools(session, household)
        # Insert a PENDING shadow tx directly via the model layer (no commit-
        # then-flip). PENDING isn't valid input for void.
        from tulip_storage.models import ShadowTransaction as StorageShadowTx
        from tulip_storage.models import ShadowTxReason as StorageShadowReason

        pending = StorageShadowTx(
            household_id=household.id,
            id=uuid4(),
            date=date(2026, 6, 1),
            description="WIP",
            reason=StorageShadowReason.MANUAL,
            status=StorageShadowStatus.PENDING,
        )
        session.add(pending)
        session.commit()

        repo = ShadowTransactionRepository(session, household.id)
        with pytest.raises(ShadowTxNotVoidableError):
            repo.void(pending.id, voided_at=datetime.now(tz=UTC))

    def test_unknown_shadow_tx_raises_lookup_error(self, session: Session, household: Household):
        repo = ShadowTransactionRepository(session, household.id)
        with pytest.raises(LookupError):
            repo.void(uuid4(), voided_at=datetime.now(tz=UTC))
