"""Test fixtures for tulip-storage."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from tulip_storage.migrations._triggers import INITIAL_TRIGGERS
from tulip_storage.models import Base


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Yield an in-memory SQLite engine with all tables and triggers created.

    Foreign-key enforcement is enabled (off by default in SQLite). The
    balance-enforcement triggers from migration 0001 are also installed so
    repository tests exercise the full safety net.
    """
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
    yield eng
    eng.dispose()


@pytest.fixture
def session_maker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def session(session_maker: sessionmaker[Session]) -> Iterator[Session]:
    with session_maker() as s:
        yield s
