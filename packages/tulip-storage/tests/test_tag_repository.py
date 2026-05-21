"""Unit tests for ``TagRepository`` (ADR-0009, PR A)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_storage.models import Household
from tulip_storage.repositories import TagRepository


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def test_get_or_create_creates_on_first_call(session: Session, household: Household) -> None:
    repo = TagRepository(session, household.id)
    tag = repo.get_or_create_by_name("birthday")
    session.commit()
    assert tag.name == "birthday"
    assert tag.household_id == household.id


def test_get_or_create_returns_existing(session: Session, household: Household) -> None:
    """Idempotent: calling twice for the same name returns the same row."""
    repo = TagRepository(session, household.id)
    first = repo.get_or_create_by_name("walter")
    session.commit()
    second = repo.get_or_create_by_name("walter")
    session.commit()
    assert first.id == second.id


def test_tag_name_is_unique_per_household(session: Session) -> None:
    """Two households can both have a 'birthday' tag — they're distinct rows."""
    h1 = Household(id=uuid4(), name="Smith", base_currency="USD")
    h2 = Household(id=uuid4(), name="Jones", base_currency="USD")
    session.add_all([h1, h2])
    session.commit()
    repo1 = TagRepository(session, h1.id)
    repo2 = TagRepository(session, h2.id)
    tag1 = repo1.get_or_create_by_name("birthday")
    tag2 = repo2.get_or_create_by_name("birthday")
    session.commit()
    assert tag1.id != tag2.id
    assert tag1.household_id == h1.id
    assert tag2.household_id == h2.id


def test_get_by_name_returns_none_for_missing(session: Session, household: Household) -> None:
    assert TagRepository(session, household.id).get_by_name("nope") is None


def test_list_all_returns_sorted(session: Session, household: Household) -> None:
    repo = TagRepository(session, household.id)
    for name in ["zebra", "alpha", "mango"]:
        repo.get_or_create_by_name(name)
    session.commit()
    names = [t.name for t in repo.list_all()]
    assert names == ["alpha", "mango", "zebra"]


def test_rename_is_o1(session: Session, household: Household) -> None:
    """Renaming a tag updates the single row; transaction_tags edges follow
    via the FK (no rewriting transaction-tag rows needed)."""
    repo = TagRepository(session, household.id)
    tag = repo.get_or_create_by_name("walter")
    session.commit()
    repo.rename(tag.id, "walter-s.")
    session.commit()
    reloaded = repo.get_by_name("walter-s.")
    assert reloaded is not None
    assert reloaded.id == tag.id
