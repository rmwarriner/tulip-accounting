"""Unit tests for derive_paired_shadow_tx (the auto-pairing engine)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tulip_core.account import AccountType
from tulip_core.allocation import (
    InvalidAccountTypePairingError,
    MultiCurrencyPoolTaggingError,
    ShadowTxReason,
    ShadowTxStatus,
    UnsupportedRefundShapedShadowTxError,
    derive_paired_shadow_tx,
)
from tulip_core.money import Money
from tulip_core.transactions import Posting, Transaction, TransactionStatus


def _build_main_tx(
    *,
    household_id: UUID,
    postings: tuple[Posting, ...],
    description: str = "Costco run",
    tx_date: date = date(2026, 6, 1),
) -> Transaction:
    return Transaction(
        id=uuid4(),
        household_id=household_id,
        date=tx_date,
        description=description,
        postings=postings,
        status=TransactionStatus.POSTED,
    )


def test_no_pool_tagged_postings_returns_none() -> None:
    household_id = uuid4()
    food_id = uuid4()
    cash_id = uuid4()
    main_tx = _build_main_tx(
        household_id=household_id,
        postings=(
            Posting(id=uuid4(), account_id=food_id, amount=Money(Decimal("12.50"), "USD")),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-12.50"), "USD")),
        ),
    )
    result = derive_paired_shadow_tx(
        main_tx,
        account_types_by_id={food_id: AccountType.EXPENSE, cash_id: AccountType.ASSET},
        spent_pool_by_currency={"USD": uuid4()},
    )
    assert result is None


def test_single_pool_spend_yields_two_legs() -> None:
    household_id = uuid4()
    food_id = uuid4()
    cash_id = uuid4()
    pool_id = uuid4()
    spent_pool_id = uuid4()
    main_tx = _build_main_tx(
        household_id=household_id,
        description="Costco",
        postings=(
            Posting(
                id=uuid4(),
                account_id=food_id,
                amount=Money(Decimal("50"), "USD"),
                pool_id=pool_id,
            ),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-50"), "USD")),
        ),
    )
    shadow = derive_paired_shadow_tx(
        main_tx,
        account_types_by_id={food_id: AccountType.EXPENSE, cash_id: AccountType.ASSET},
        spent_pool_by_currency={"USD": spent_pool_id},
    )
    assert shadow is not None
    assert shadow.status is ShadowTxStatus.POSTED
    assert shadow.reason is ShadowTxReason.SPEND
    assert shadow.paired_main_tx_id == main_tx.id
    assert shadow.household_id == household_id
    assert shadow.date == main_tx.date
    assert shadow.description == "Costco (envelope effects)"
    assert len(shadow.postings) == 2
    pool_leg = next(p for p in shadow.postings if p.pool_id == pool_id)
    spent_leg = next(p for p in shadow.postings if p.pool_id == spent_pool_id)
    assert pool_leg.amount == Money(Decimal("-50"), "USD")
    assert spent_leg.amount == Money(Decimal("50"), "USD")


def test_multi_pool_spend_yields_one_paired_with_three_legs() -> None:
    """ADR-0001 section B verbatim: $50 groceries + $30 entertainment + -$80 cash."""
    household_id = uuid4()
    food_id = uuid4()
    ent_id = uuid4()
    cash_id = uuid4()
    groceries_pool = uuid4()
    ent_pool = uuid4()
    spent_pool_id = uuid4()
    main_tx = _build_main_tx(
        household_id=household_id,
        postings=(
            Posting(
                id=uuid4(),
                account_id=food_id,
                amount=Money(Decimal("50"), "USD"),
                pool_id=groceries_pool,
            ),
            Posting(
                id=uuid4(),
                account_id=ent_id,
                amount=Money(Decimal("30"), "USD"),
                pool_id=ent_pool,
            ),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-80"), "USD")),
        ),
    )
    shadow = derive_paired_shadow_tx(
        main_tx,
        account_types_by_id={
            food_id: AccountType.EXPENSE,
            ent_id: AccountType.EXPENSE,
            cash_id: AccountType.ASSET,
        },
        spent_pool_by_currency={"USD": spent_pool_id},
    )
    assert shadow is not None
    assert len(shadow.postings) == 3
    by_pool = {p.pool_id: p.amount for p in shadow.postings}
    assert by_pool[groceries_pool] == Money(Decimal("-50"), "USD")
    assert by_pool[ent_pool] == Money(Decimal("-30"), "USD")
    assert by_pool[spent_pool_id] == Money(Decimal("80"), "USD")
    # Per-currency balance must be zero (it's the trigger invariant too).
    assert shadow.balance_per_currency() == {"USD": Decimal("0")}


def test_non_expense_account_type_rejected() -> None:
    household_id = uuid4()
    food_id = uuid4()
    cash_id = uuid4()
    pool_id = uuid4()
    main_tx = _build_main_tx(
        household_id=household_id,
        postings=(
            Posting(
                id=uuid4(),
                account_id=cash_id,
                amount=Money(Decimal("50"), "USD"),
                pool_id=pool_id,
            ),
            Posting(id=uuid4(), account_id=food_id, amount=Money(Decimal("-50"), "USD")),
        ),
    )
    with pytest.raises(InvalidAccountTypePairingError, match="EXPENSE"):
        derive_paired_shadow_tx(
            main_tx,
            account_types_by_id={cash_id: AccountType.ASSET, food_id: AccountType.EXPENSE},
            spent_pool_by_currency={"USD": uuid4()},
        )


def test_multi_currency_pool_tagging_rejected() -> None:
    household_id = uuid4()
    food_id = uuid4()
    travel_id = uuid4()
    cash_id = uuid4()
    main_tx = Transaction(
        id=uuid4(),
        household_id=household_id,
        date=date(2026, 6, 1),
        description="Mixed currency",
        postings=(
            Posting(
                id=uuid4(),
                account_id=food_id,
                amount=Money(Decimal("50"), "USD"),
                pool_id=uuid4(),
            ),
            Posting(
                id=uuid4(),
                account_id=travel_id,
                amount=Money(Decimal("30"), "EUR"),
                pool_id=uuid4(),
            ),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-50"), "USD")),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-30"), "EUR")),
        ),
        # PENDING because the multi-currency tx is balanced per currency
        # but we don't want post-init to enforce; PENDING is exempt and
        # the engine doesn't care about main-tx status.
        status=TransactionStatus.PENDING,
    )
    with pytest.raises(MultiCurrencyPoolTaggingError):
        derive_paired_shadow_tx(
            main_tx,
            account_types_by_id={
                food_id: AccountType.EXPENSE,
                travel_id: AccountType.EXPENSE,
                cash_id: AccountType.ASSET,
            },
            spent_pool_by_currency={"USD": uuid4(), "EUR": uuid4()},
        )


def test_refund_shaped_pool_effect_rejected() -> None:
    """A negative-amount EXPENSE posting (refund in) gives positive net pool effect."""
    household_id = uuid4()
    food_id = uuid4()
    cash_id = uuid4()
    pool_id = uuid4()
    main_tx = Transaction(
        id=uuid4(),
        household_id=household_id,
        date=date(2026, 6, 1),
        description="Refund",
        postings=(
            Posting(
                id=uuid4(),
                account_id=food_id,
                amount=Money(Decimal("-50"), "USD"),  # refund: negative expense
                pool_id=pool_id,
            ),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("50"), "USD")),
        ),
        status=TransactionStatus.POSTED,
    )
    with pytest.raises(UnsupportedRefundShapedShadowTxError):
        derive_paired_shadow_tx(
            main_tx,
            account_types_by_id={food_id: AccountType.EXPENSE, cash_id: AccountType.ASSET},
            spent_pool_by_currency={"USD": uuid4()},
        )


def test_missing_spent_pool_for_currency_raises() -> None:
    """Caller is responsible for materializing system pools — engine raises if missing."""
    household_id = uuid4()
    food_id = uuid4()
    cash_id = uuid4()
    pool_id = uuid4()
    main_tx = _build_main_tx(
        household_id=household_id,
        postings=(
            Posting(
                id=uuid4(),
                account_id=food_id,
                amount=Money(Decimal("50"), "USD"),
                pool_id=pool_id,
            ),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-50"), "USD")),
        ),
    )
    with pytest.raises(ValueError, match="Spent system pool"):
        derive_paired_shadow_tx(
            main_tx,
            account_types_by_id={food_id: AccountType.EXPENSE, cash_id: AccountType.ASSET},
            spent_pool_by_currency={},  # no Spent pool registered
        )


def test_unknown_account_id_in_resolver_treated_as_invalid_type() -> None:
    household_id = uuid4()
    food_id = uuid4()
    cash_id = uuid4()
    pool_id = uuid4()
    main_tx = _build_main_tx(
        household_id=household_id,
        postings=(
            Posting(
                id=uuid4(),
                account_id=food_id,
                amount=Money(Decimal("50"), "USD"),
                pool_id=pool_id,
            ),
            Posting(id=uuid4(), account_id=cash_id, amount=Money(Decimal("-50"), "USD")),
        ),
    )
    # food_id missing from account_types_by_id — engine treats it as
    # "unknown type" → InvalidAccountTypePairingError. The router's
    # responsibility is to populate the resolver from a real repo.
    with pytest.raises(InvalidAccountTypePairingError, match="unknown"):
        derive_paired_shadow_tx(
            main_tx,
            account_types_by_id={cash_id: AccountType.ASSET},
            spent_pool_by_currency={"USD": uuid4()},
        )
