"""Unit tests for ``PostingTagRepository`` + ``AccountTagRepository`` (ADR-0009, PR B)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
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
)
from tulip_storage.repositories import (
    AccountRepository,
    AccountTagRepository,
    PeriodRepository,
    PostingTagRepository,
    TransactionRepository,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


@pytest.fixture
def two_accounts(session: Session, household: Household) -> tuple[Account, Account]:
    repo = AccountRepository(session, household.id)
    cash = repo.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
    food = repo.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
    session.commit()
    return cash, food


def test_account_tag_set_get_round_trip(
    session: Session, household: Household, two_accounts: tuple[Account, Account]
) -> None:
    cash, _food = two_accounts
    repo = AccountTagRepository(session, household.id)
    saved = repo.set_tags(cash.id, ["joint", "credit-card"])
    session.commit()
    # Stored set is deduplicated + sorted.
    assert saved == ["credit-card", "joint"]
    assert repo.list_tags(cash.id) == ["credit-card", "joint"]


def test_account_tag_set_replaces_previous(
    session: Session, household: Household, two_accounts: tuple[Account, Account]
) -> None:
    cash, _food = two_accounts
    repo = AccountTagRepository(session, household.id)
    repo.set_tags(cash.id, ["a", "b"])
    session.commit()
    repo.set_tags(cash.id, ["c"])
    session.commit()
    assert repo.list_tags(cash.id) == ["c"]


def test_account_tag_find_by_tag(
    session: Session, household: Household, two_accounts: tuple[Account, Account]
) -> None:
    cash, food = two_accounts
    repo = AccountTagRepository(session, household.id)
    repo.set_tags(cash.id, ["joint"])
    repo.set_tags(food.id, ["joint"])
    session.commit()
    found = repo.find_account_ids_by_tag("joint")
    assert set(found) == {cash.id, food.id}


def test_account_tag_unknown_tag_returns_empty(session: Session, household: Household) -> None:
    repo = AccountTagRepository(session, household.id)
    assert repo.find_account_ids_by_tag("nope") == []


def test_posting_tag_round_trip(
    session: Session, household: Household, two_accounts: tuple[Account, Account]
) -> None:
    cash, food = two_accounts
    # Seed a transaction so we have postings.
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

    # Look up the posting rows directly — Transaction doesn't expose
    # a postings relationship today.
    from sqlalchemy import select

    from tulip_storage.models import Posting

    rows = (
        session.execute(select(Posting).where(Posting.transaction_id == domain_tx.id))
        .scalars()
        .all()
    )
    food_posting_id = next(p.id for p in rows if p.account_id == food.id)
    posting_tags = PostingTagRepository(session, household.id)
    saved_tags = posting_tags.set_tags(food_posting_id, ["walter", "birthday"])
    session.commit()
    assert saved_tags == ["birthday", "walter"]
    assert posting_tags.list_tags(food_posting_id) == ["birthday", "walter"]


def test_posting_tag_find_by_tag(
    session: Session, household: Household, two_accounts: tuple[Account, Account]
) -> None:
    """find_posting_ids_by_tag returns every posting carrying the tag."""
    cash, food = two_accounts
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

    from sqlalchemy import select

    from tulip_storage.models import Posting

    rows = (
        session.execute(select(Posting).where(Posting.transaction_id == domain_tx.id))
        .scalars()
        .all()
    )

    posting_tags = PostingTagRepository(session, household.id)
    food_posting_id = next(p.id for p in rows if p.account_id == food.id)
    posting_tags.set_tags(food_posting_id, ["walter"])
    session.commit()
    assert posting_tags.find_posting_ids_by_tag("walter") == [food_posting_id]
    assert posting_tags.find_posting_ids_by_tag("nope") == []


def test_batch_list_tags_for_postings(
    session: Session, household: Household, two_accounts: tuple[Account, Account]
) -> None:
    """list_tags_for_postings returns the batch map keyed by posting id."""
    cash, food = two_accounts
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

    from sqlalchemy import select

    from tulip_storage.models import Posting

    rows = (
        session.execute(select(Posting).where(Posting.transaction_id == domain_tx.id))
        .scalars()
        .all()
    )

    food_posting_id = next(p.id for p in rows if p.account_id == food.id)
    cash_posting_id = next(p.id for p in rows if p.account_id == cash.id)

    posting_tags = PostingTagRepository(session, household.id)
    posting_tags.set_tags(food_posting_id, ["a"])
    posting_tags.set_tags(cash_posting_id, ["b", "c"])
    session.commit()

    batch = posting_tags.list_tags_for_postings([food_posting_id, cash_posting_id])
    assert batch[food_posting_id] == ["a"]
    assert batch[cash_posting_id] == ["b", "c"]
