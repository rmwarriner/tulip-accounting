"""Tests for the Pool value object and PoolType enum."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tulip_core.allocation import Pool, PoolType


def test_pool_construction_minimal() -> None:
    p = Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
    )
    assert p.is_active is True
    assert p.is_system is False
    assert p.visibility == "shared"


def test_pool_unknown_currency_rejected() -> None:
    with pytest.raises(ValueError):
        Pool(
            id=uuid4(),
            household_id=uuid4(),
            pool_type=PoolType.ENVELOPE,
            name="Groceries",
            currency="ZZZ",
        )


def test_pool_empty_name_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Pool(
            id=uuid4(),
            household_id=uuid4(),
            pool_type=PoolType.ENVELOPE,
            name="",
            currency="USD",
        )


def test_pool_unknown_visibility_rejected() -> None:
    with pytest.raises(ValueError, match="visibility"):
        Pool(
            id=uuid4(),
            household_id=uuid4(),
            pool_type=PoolType.ENVELOPE,
            name="Groceries",
            currency="USD",
            visibility="public",
        )


@pytest.mark.parametrize("system_type", [PoolType.INFLOW, PoolType.UNALLOCATED, PoolType.SPENT])
def test_system_pool_type_requires_is_system(system_type: PoolType) -> None:
    with pytest.raises(ValueError, match="is_system=True"):
        Pool(
            id=uuid4(),
            household_id=uuid4(),
            pool_type=system_type,
            name="x",
            currency="USD",
            is_system=False,
        )


@pytest.mark.parametrize("user_type", [PoolType.ENVELOPE, PoolType.SINKING_FUND])
def test_user_pool_type_forbids_is_system(user_type: PoolType) -> None:
    with pytest.raises(ValueError, match="cannot have is_system=True"):
        Pool(
            id=uuid4(),
            household_id=uuid4(),
            pool_type=user_type,
            name="x",
            currency="USD",
            is_system=True,
        )


def test_system_pool_must_be_shared() -> None:
    with pytest.raises(ValueError, match="visibility='shared'"):
        Pool(
            id=uuid4(),
            household_id=uuid4(),
            pool_type=PoolType.INFLOW,
            name="Inflow USD",
            currency="USD",
            is_system=True,
            visibility="private",
        )


def test_pool_equality_by_id() -> None:
    pool_id = uuid4()
    a = Pool(
        id=pool_id,
        household_id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
    )
    b = Pool(
        id=pool_id,
        household_id=uuid4(),
        pool_type=PoolType.SINKING_FUND,
        name="Different",
        currency="EUR",
    )
    assert a == b
    assert hash(a) == hash(b)


def test_pool_inequality_with_other_types() -> None:
    p = Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
    )
    assert p != "not a pool"
    assert p != 42


def test_pool_is_frozen() -> None:
    p = Pool(
        id=uuid4(),
        household_id=uuid4(),
        pool_type=PoolType.ENVELOPE,
        name="Groceries",
        currency="USD",
    )
    with pytest.raises(AttributeError):
        p.name = "Renamed"  # type: ignore[misc]
