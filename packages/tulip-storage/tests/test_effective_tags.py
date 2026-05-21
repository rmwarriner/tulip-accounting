"""Tests for ``EffectiveTagsRepository`` — ADR-0009 PR C."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tulip_core.money import Money
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import (
    Account,
    AccountType,
    Household,
    PeriodStatus,
    Posting,
)
from tulip_storage.repositories import (
    AccountRepository,
    AccountTagRepository,
    EffectiveTagsRepository,
    PeriodRepository,
    PostingTagRepository,
    TransactionRepository,
    TransactionTagRepository,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


@pytest.fixture
def chart(session: Session, household: Household) -> tuple[Account, Account]:
    repo = AccountRepository(session, household.id)
    cash = repo.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
    food = repo.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
    session.commit()
    return cash, food


def _seed_balanced_tx(
    session: Session, household: Household, cash: Account, food: Account
) -> tuple[Posting, Posting, DomainTransaction]:
    """Seed one balanced transaction, return its two postings."""
    PeriodRepository(session, household.id).create(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=PeriodStatus.OPEN,
    )
    session.commit()
    tx_repo = TransactionRepository(session, household.id)
    domain_tx = DomainTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 5, 1),
        description="Lunch",
        postings=(
            DomainPosting(id=uuid4(), account_id=food.id, amount=Money(Decimal("10"), "USD")),
            DomainPosting(id=uuid4(), account_id=cash.id, amount=Money(Decimal("-10"), "USD")),
        ),
        status=DomainTxStatus.POSTED,
    )
    tx_repo.save_balanced(domain_tx)
    session.commit()
    rows = (
        session.execute(select(Posting).where(Posting.transaction_id == domain_tx.id))
        .scalars()
        .all()
    )
    food_p = next(p for p in rows if p.account_id == food.id)
    cash_p = next(p for p in rows if p.account_id == cash.id)
    return food_p, cash_p, domain_tx


def test_for_posting_returns_direct_only_when_no_inheritance(
    session: Session, household: Household, chart: tuple[Account, Account]
) -> None:
    cash, food = chart
    food_p, _cash_p, _tx = _seed_balanced_tx(session, household, cash, food)
    PostingTagRepository(session, household.id).set_tags(food_p.id, ["walter"])
    session.commit()

    eff = EffectiveTagsRepository(session, household.id).for_posting(food_p.id)
    assert len(eff) == 1
    assert eff[0].name == "walter"
    assert eff[0].provenance == "posting"
    assert eff[0].source_id == food_p.id


def test_for_posting_inherits_from_transaction_and_account(
    session: Session, household: Household, chart: tuple[Account, Account]
) -> None:
    """Posting effective tags include direct + transaction-level + account-level."""
    cash, food = chart
    food_p, _cash_p, domain_tx = _seed_balanced_tx(session, household, cash, food)
    PostingTagRepository(session, household.id).set_tags(food_p.id, ["walter"])
    TransactionTagRepository(session, household.id).set_tags(domain_tx.id, ["birthday"])
    AccountTagRepository(session, household.id).set_tags(food.id, ["essential"])
    session.commit()

    eff = EffectiveTagsRepository(session, household.id).for_posting(food_p.id)
    by_provenance = {(t.provenance, t.name) for t in eff}
    assert by_provenance == {
        ("posting", "walter"),
        ("transaction", "birthday"),
        ("account", "essential"),
    }


def test_for_posting_unknown_returns_empty(session: Session, household: Household) -> None:
    eff = EffectiveTagsRepository(session, household.id).for_posting(uuid4())
    assert eff == []


def test_for_transaction_unions_direct_posting_and_account_tags(
    session: Session, household: Household, chart: tuple[Account, Account]
) -> None:
    """Transaction effective tags = direct + every posting's posting + account tags."""
    cash, food = chart
    food_p, cash_p, domain_tx = _seed_balanced_tx(session, household, cash, food)

    TransactionTagRepository(session, household.id).set_tags(domain_tx.id, ["birthday"])
    PostingTagRepository(session, household.id).set_tags(food_p.id, ["walter"])
    PostingTagRepository(session, household.id).set_tags(cash_p.id, ["check-pmt"])
    AccountTagRepository(session, household.id).set_tags(food.id, ["essential"])
    AccountTagRepository(session, household.id).set_tags(cash.id, ["joint"])
    session.commit()

    eff = EffectiveTagsRepository(session, household.id).for_transaction(domain_tx.id)
    by_provenance = {(t.provenance, t.name) for t in eff}
    assert by_provenance == {
        ("transaction", "birthday"),
        ("posting", "walter"),
        ("posting", "check-pmt"),
        ("account", "essential"),
        ("account", "joint"),
    }


def test_for_transaction_returns_empty_when_no_postings_or_tags(
    session: Session, household: Household
) -> None:
    eff = EffectiveTagsRepository(session, household.id).for_transaction(uuid4())
    assert eff == []


def test_sorting_is_deterministic(
    session: Session, household: Household, chart: tuple[Account, Account]
) -> None:
    """Same edges → same result list, sorted (provenance, name)."""
    cash, food = chart
    food_p, _cash_p, domain_tx = _seed_balanced_tx(session, household, cash, food)
    PostingTagRepository(session, household.id).set_tags(food_p.id, ["zebra", "alpha"])
    TransactionTagRepository(session, household.id).set_tags(domain_tx.id, ["mango", "apple"])
    session.commit()

    eff = EffectiveTagsRepository(session, household.id).for_posting(food_p.id)
    keys = [(t.provenance, t.name) for t in eff]
    # account < posting < transaction alphabetically.
    assert keys == [
        ("posting", "alpha"),
        ("posting", "zebra"),
        ("transaction", "apple"),
        ("transaction", "mango"),
    ]
