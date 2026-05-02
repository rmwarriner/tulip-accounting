"""Tests for ShadowTransaction's balance-invariant enforcement."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from tulip_core.allocation import (
    ShadowPosting,
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
)
from tulip_core.money import Money


def _balanced_postings(currency: str = "USD") -> tuple[ShadowPosting, ShadowPosting]:
    return (
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("250"), currency)),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-250"), currency)),
    )


def test_pending_can_be_unbalanced() -> None:
    postings = (
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("100"), "USD")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-50"), "USD")),
    )
    tx = ShadowTransaction(
        id=uuid4(),
        household_id=uuid4(),
        date=date(2026, 6, 1),
        description="WIP",
        reason=ShadowTxReason.MANUAL,
        postings=postings,
        status=ShadowTxStatus.PENDING,
    )
    assert tx.is_balanced() is False


def test_posted_must_be_balanced() -> None:
    postings = (
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("100"), "USD")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-50"), "USD")),
    )
    with pytest.raises(ValueError, match="balance per currency"):
        ShadowTransaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 6, 1),
            description="Bad",
            reason=ShadowTxReason.MANUAL,
            postings=postings,
            status=ShadowTxStatus.POSTED,
        )


def test_balanced_posted_succeeds() -> None:
    tx = ShadowTransaction(
        id=uuid4(),
        household_id=uuid4(),
        date=date(2026, 6, 1),
        description="Refill",
        reason=ShadowTxReason.REFILL,
        postings=_balanced_postings(),
        status=ShadowTxStatus.POSTED,
    )
    assert tx.is_balanced()
    assert tx.status is ShadowTxStatus.POSTED


def test_must_have_two_postings() -> None:
    only_one = (ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("0"), "USD")),)
    with pytest.raises(ValueError, match="at least two"):
        ShadowTransaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 6, 1),
            description="x",
            reason=ShadowTxReason.MANUAL,
            postings=only_one,
            status=ShadowTxStatus.PENDING,
        )


def test_balance_per_currency_segregates_currencies() -> None:
    # USD legs must zero; EUR legs must zero. Cross-currency net does NOT balance.
    postings = (
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("100"), "USD")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-100"), "USD")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("80"), "EUR")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-80"), "EUR")),
    )
    tx = ShadowTransaction(
        id=uuid4(),
        household_id=uuid4(),
        date=date(2026, 6, 1),
        description="Multi-ccy",
        reason=ShadowTxReason.TRANSFER,
        postings=postings,
        status=ShadowTxStatus.POSTED,
    )
    assert tx.balance_per_currency() == {"USD": Decimal("0"), "EUR": Decimal("0")}


def test_posted_rejects_cross_currency_imbalance() -> None:
    # USD nets to zero, EUR doesn't.
    postings = (
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("100"), "USD")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-100"), "USD")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("80"), "EUR")),
        ShadowPosting(id=uuid4(), pool_id=uuid4(), amount=Money(Decimal("-50"), "EUR")),
    )
    with pytest.raises(ValueError, match="EUR"):
        ShadowTransaction(
            id=uuid4(),
            household_id=uuid4(),
            date=date(2026, 6, 1),
            description="Bad",
            reason=ShadowTxReason.TRANSFER,
            postings=postings,
            status=ShadowTxStatus.POSTED,
        )


def test_paired_main_tx_id_optional() -> None:
    paired_id = uuid4()
    tx = ShadowTransaction(
        id=uuid4(),
        household_id=uuid4(),
        date=date(2026, 6, 1),
        description="Costco shadow",
        reason=ShadowTxReason.SPEND,
        postings=_balanced_postings(),
        status=ShadowTxStatus.POSTED,
        paired_main_tx_id=paired_id,
    )
    assert tx.paired_main_tx_id == paired_id
