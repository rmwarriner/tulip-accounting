"""FastAPI dependency-injection wiring.

In tests, `get_session` and `get_settings` are overridden via
`app.dependency_overrides`. In production they read from process state
configured by the Settings object.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tulip_api.config import get_settings


@lru_cache(maxsize=1)
def _engine_for(url: str) -> Engine:
    eng = create_engine(url, future=True)
    if eng.dialect.name == "sqlite":

        @event.listens_for(eng, "connect")
        def _fk(dc: object, _r: object) -> None:
            cur = dc.cursor()  # type: ignore[attr-defined]
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


@lru_cache(maxsize=1)
def _session_factory_for(url: str) -> sessionmaker[Session]:
    return sessionmaker(_engine_for(url), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a Session bound to the configured database URL."""
    settings = get_settings()
    factory = _session_factory_for(settings.database_url)
    with factory() as s:
        try:
            yield s
        finally:
            s.close()
