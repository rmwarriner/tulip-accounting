"""FastAPI app factory for Tulip Accounting.

`create_app()` builds the app with all routers, middleware, and lifespan
hooks attached. Tests construct fresh apps via this factory so each test
runs against an isolated, predictable instance.

The lifespan hook starts the scheduler runner (P4.3.a / ADR-0002) on
app startup and stops it cleanly on shutdown. The runner picks up the
session factory from the configured Settings, so tests that override
``get_session`` should also call ``app.state.runner = None`` (or
``disable_runner=True`` on construction) to avoid spinning up a real
async loop alongside the in-process test session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tulip_api.config import get_settings
from tulip_api.errors import install_problem_handlers
from tulip_api.logging_config import configure_logging
from tulip_api.middleware import RequestIdMiddleware
from tulip_api.routers import (
    accounts,
    auth,
    envelopes,
    health,
    imports,
    pools,
    refill_schedules,
    reports,
    sinking_funds,
    transactions,
    well_known_errors,
)
from tulip_storage.runner import Runner

API_VERSION = "v1"
API_TITLE = "Tulip Accounting API"


def _build_session_maker(database_url: str) -> sessionmaker[Session]:
    """Mirror the deps.py factory but eager — the runner can't lazy-init."""
    eng = create_engine(database_url, future=True)
    if eng.dialect.name == "sqlite":

        @event.listens_for(eng, "connect")
        def _fk(dc: object, _r: object) -> None:
            cur = dc.cursor()  # type: ignore[attr-defined]
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return sessionmaker(eng, expire_on_commit=False)


def create_app(*, enable_runner: bool = True) -> FastAPI:
    """Build a fresh FastAPI app instance.

    Args:
        enable_runner: When True (default), the FastAPI lifespan starts an
            in-process scheduler runner per ADR-0002. Tests that override
            ``get_session`` to point at an in-memory DB pass ``False`` to
            skip the runner — its session factory wouldn't see the
            test's overridden DB without extra plumbing.

    """
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runner: Runner | None = None
        if enable_runner:
            settings = get_settings()
            session_maker = _build_session_maker(settings.database_url)
            runner = Runner(session_maker)
            app.state.runner = runner
            await runner.start()
        else:
            app.state.runner = None
        try:
            yield
        finally:
            if runner is not None:
                await runner.stop()

    app = FastAPI(
        title=API_TITLE,
        version="0.1.0",
        description=(
            "Household-focused double-entry accounting API. See ARCHITECTURE.md for design notes."
        ),
        lifespan=lifespan,
    )

    # Request-id stamping must run before any router-level logging so the
    # request_id is in scope for every log line emitted during handling.
    app.add_middleware(RequestIdMiddleware)

    install_problem_handlers(app)

    # Top-level health probe — kept off /v1 so monitors don't break across
    # major-version cuts.
    app.include_router(health.router)
    app.include_router(well_known_errors.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(transactions.router)
    app.include_router(envelopes.router)
    app.include_router(sinking_funds.router)
    app.include_router(pools.router)
    app.include_router(refill_schedules.router)
    app.include_router(reports.router)
    app.include_router(imports.router)

    return app
