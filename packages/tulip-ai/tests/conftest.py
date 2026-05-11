"""tulip-ai test fixtures — reuses the storage-package conftest pattern."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.migrations._triggers import INITIAL_TRIGGERS, P4_0_SHADOW_TRIGGERS
from tulip_storage.models import Base, Household, User, UserRole


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite with all tables + triggers created."""
    eng = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        for ddl in INITIAL_TRIGGERS:
            conn.execute(text(ddl))
        for ddl in P4_0_SHADOW_TRIGGERS:
            conn.execute(text(ddl))
    yield eng
    eng.dispose()


@pytest.fixture
def session_maker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def master_key() -> bytes:
    return b"\xab" * 32


@pytest.fixture
def household_and_user(
    session_maker: sessionmaker[Session],
) -> tuple[Household, User]:
    """Seed one household with one admin user."""
    with session_maker() as s:
        h = Household(id=uuid4(), name="Test House", base_currency="USD")
        s.add(h)
        s.flush()
        u = User(
            household_id=h.id,
            id=uuid4(),
            email="admin@example.com",
            password_hash="$argon2i$dummy",
            display_name="Admin",
            role=UserRole.ADMIN,
        )
        s.add(u)
        s.commit()
        s.refresh(h)
        s.refresh(u)
        return h, u
