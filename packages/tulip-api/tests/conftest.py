"""Shared test fixtures for the FastAPI app.

Each test gets a fresh tmp_path SQLite DB with the full migration applied,
plus a settings override that points the app at it. The app's session
dependency is overridden so handlers run inside the test's session scope.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

# Make sibling test-helper modules (e.g. _problem_details.py) importable by
# basename. pytest's importlib mode doesn't put the tests/ dir on sys.path.
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from alembic.command import upgrade
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.main import create_app

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "tulip-storage" / "alembic.ini"


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(_ALEMBIC_INI.parent / "src" / "tulip_storage" / "migrations"),
    )
    return cfg


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'tulip.db'}"
    upgrade(_make_alembic_cfg(url), "head")
    return url


@pytest.fixture
def session_maker(db_url: str) -> sessionmaker[Session]:
    eng = create_engine(db_url, future=True)

    @event.listens_for(eng, "connect")
    def _fk(dc, _r):  # type: ignore[no-untyped-def]
        c = dc.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    return sessionmaker(eng, expire_on_commit=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",  # overridden per-app via deps
        jwt_secret="test-secret-32bytes-test-secret!!",
        master_key=b"\xab" * 32,  # deterministic test key; never used outside tests
    )


@pytest.fixture
def app(session_maker: sessionmaker[Session], settings: Settings) -> Iterator[FastAPI]:
    # Disable the scheduler runner per ADR-0002; tests run synchronously
    # against TestClient and the runner's own session factory won't see
    # the per-test overridden DB. Runner-specific tests opt back in via
    # a separate fixture.
    a = create_app(enable_runner=False)

    def _override_session() -> Iterator[Session]:
        with session_maker() as s:
            yield s

    a.dependency_overrides[get_session] = _override_session
    a.dependency_overrides[get_settings] = lambda: settings
    yield a
    a.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)
