"""Tests for ``AccountRepository.find_by_name_path`` (#450).

Powers the QIF import-apply category resolution: a Banktivity-style
\"Wants:Personal:Gifts\" must match a GnuCash-rooted chart's
\"Expenses:Wants:Personal:Gifts\" without forcing the operator to
re-tag every category.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_storage.models import Account, AccountType, Household
from tulip_storage.repositories import AccountRepository


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _create_chain(
    session: Session,
    household: Household,
    *,
    type_: AccountType = AccountType.EXPENSE,
    names: list[str],
) -> Account:
    """Build a hierarchical chain of accounts; return the leaf."""
    repo = AccountRepository(session, household.id)
    parent_id = None
    leaf: Account | None = None
    for name in names:
        leaf = repo.create(
            name=name,
            type=type_,
            currency="USD",
            parent_account_id=parent_id,
        )
        parent_id = leaf.id
    session.commit()
    assert leaf is not None
    return leaf


def test_matches_with_type_prefix(session: Session, household: Household) -> None:
    """``Expenses:Wants:Personal:Gifts`` resolves to the leaf."""
    leaf = _create_chain(session, household, names=["Expenses", "Wants", "Personal", "Gifts"])
    repo = AccountRepository(session, household.id)
    found = repo.find_by_name_path("Expenses:Wants:Personal:Gifts")
    assert found is not None
    assert found.id == leaf.id


def test_matches_suffix_without_type_prefix(session: Session, household: Household) -> None:
    """``Wants:Personal:Gifts`` (no type root) still resolves — the chart
    has ``Expenses:Wants:Personal:Gifts`` and the path matches the
    trailing suffix."""
    leaf = _create_chain(session, household, names=["Expenses", "Wants", "Personal", "Gifts"])
    repo = AccountRepository(session, household.id)
    found = repo.find_by_name_path("Wants:Personal:Gifts")
    assert found is not None
    assert found.id == leaf.id


def test_case_insensitive_match(session: Session, household: Household) -> None:
    leaf = _create_chain(session, household, names=["Expenses", "Wants", "Personal", "Gifts"])
    repo = AccountRepository(session, household.id)
    found = repo.find_by_name_path("wants:PERSONAL:Gifts")
    assert found is not None
    assert found.id == leaf.id


def test_returns_none_on_no_match(session: Session, household: Household) -> None:
    _create_chain(session, household, names=["Expenses", "Wants", "Gifts"])
    repo = AccountRepository(session, household.id)
    assert repo.find_by_name_path("Nope:Whatever") is None


def test_returns_none_on_ambiguous_match(session: Session, household: Household) -> None:
    """Two siblings with the same name → ambiguous; return None."""
    repo = AccountRepository(session, household.id)
    parent = repo.create(name="Expenses", type=AccountType.EXPENSE, currency="USD")
    branch_a = repo.create(
        name="Wants",
        type=AccountType.EXPENSE,
        currency="USD",
        parent_account_id=parent.id,
    )
    branch_b = repo.create(
        name="Wants",
        type=AccountType.EXPENSE,
        currency="USD",
        parent_account_id=parent.id,
    )
    repo.create(
        name="Gifts",
        type=AccountType.EXPENSE,
        currency="USD",
        parent_account_id=branch_a.id,
    )
    repo.create(
        name="Gifts",
        type=AccountType.EXPENSE,
        currency="USD",
        parent_account_id=branch_b.id,
    )
    session.commit()
    assert repo.find_by_name_path("Wants:Gifts") is None


def test_type_prefix_constrains_search(session: Session, household: Household) -> None:
    """``Income:Salary`` doesn't match an expense account named ``Salary``."""
    _create_chain(session, household, names=["Salary"], type_=AccountType.EXPENSE)
    repo = AccountRepository(session, household.id)
    # Without type prefix the leaf matches.
    assert repo.find_by_name_path("Salary") is not None
    # With ``Income:`` constraint it doesn't (it's an expense).
    assert repo.find_by_name_path("Income:Salary") is None


def test_strips_tag_suffix(session: Session, household: Household) -> None:
    """Defensive: ``<path>/<tag>`` resolves to the path's leaf
    (the parser-side fix is #447; the resolver shouldn't be picky)."""
    leaf = _create_chain(session, household, names=["Expenses", "Wants", "Gifts"])
    repo = AccountRepository(session, household.id)
    found = repo.find_by_name_path("Wants:Gifts/Birthday")
    assert found is not None
    assert found.id == leaf.id


def test_empty_or_whitespace_returns_none(session: Session, household: Household) -> None:
    repo = AccountRepository(session, household.id)
    assert repo.find_by_name_path("") is None
    assert repo.find_by_name_path("   ") is None
    assert repo.find_by_name_path(":::") is None
