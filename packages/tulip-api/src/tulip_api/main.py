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
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tulip_api.auth.rate_limit import auth_rate_limit_handler, limiter
from tulip_api.config import get_settings
from tulip_api.errors import install_problem_handlers
from tulip_api.logging_config import configure_logging
from tulip_api.middleware import RequestIdMiddleware
from tulip_api.routers import (
    accounts,
    admin,
    ai,
    auth,
    csv_profiles,
    envelopes,
    health,
    households,
    imports,
    journal,
    notifications,
    periods,
    pools,
    proposals,
    reconciliations,
    refill_schedules,
    reports,
    sinking_funds,
    system,
    transactions,
    users,
    well_known_errors,
)
from tulip_core.reconciliation.categorizer import register_categorizer
from tulip_storage.runner import Runner

# Phase 6 / P6.1: AICategorizer replaces NullCategorizer at this seam.
# The categorizer itself decides per-call whether to issue a real AI call
# (presence of an API key + non-disabled policy) or fall back to
# "Imbalance:Unknown" — so registering it unconditionally is safe even
# for households that haven't configured AI yet. The session factory is
# bound at lifespan startup once the configured DB URL is in scope; see
# ``lifespan()`` below.
_ai_categorizer_registered = False


def _register_ai_categorizer(session_maker: sessionmaker[Session]) -> None:
    """Wire ``AICategorizer`` into the global ``Categorizer`` registry.

    Idempotent — only the first call has effect. Subsequent calls would
    emit the registry's "double registration" warning, which is the right
    signal in production but noisy in tests that spin up multiple apps.
    """
    global _ai_categorizer_registered
    if _ai_categorizer_registered:
        return
    from tulip_ai import AICategorizer, LitellmAdapter

    settings = get_settings()
    register_categorizer(
        AICategorizer(
            session_maker=session_maker,
            master_key=settings.master_key,
            adapter=LitellmAdapter(),
        )
    )
    _ai_categorizer_registered = True


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
            # H-3 (#235): register the attachment GC handler so orphaned
            # ciphertext files get swept periodically. The runner reads
            # ``scheduled_jobs`` for the actual fire cadence; an installer
            # is expected to seed a daily rrule for ``attachment_gc``.
            from tulip_storage.runner.handlers import (
                make_ai_retention_handler,
                make_attachment_gc_handler,
                make_audit_retention_handler,
            )

            runner.register_handler(
                "attachment_gc",
                make_attachment_gc_handler(session_maker, settings.attachment_root),
            )
            # H-16 (#243): TTL garbage-collection of ai_invocations. Like
            # attachment_gc, the runner reads ``scheduled_jobs`` for the
            # fire cadence; an installer seeds a daily rrule.
            runner.register_handler(
                "ai_retention",
                make_ai_retention_handler(session_maker),
            )
            # M-1 (#245): tiered TTL prune of audit_log rows. The handler
            # reads each household's ``audit_retention_policy`` (or the
            # code defaults for any unset tier) and ages out rows past
            # their tier's cutoff. Installer seeds a daily rrule.
            runner.register_handler(
                "audit_retention",
                make_audit_retention_handler(session_maker),
            )
            app.state.runner = runner
            _register_ai_categorizer(session_maker)
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

    # H-4 (#219): slowapi limiter for /v1/auth/* — wires the limiter into
    # the app state and registers a Problem-Details exception handler for
    # RateLimitExceeded. Per-route quotas are declared on each endpoint.
    app.state.limiter = limiter
    # Starlette types the handler arg as Exception; our narrower
    # RateLimitExceeded signature is correct at runtime (Starlette
    # dispatches by isinstance) and clearer at the callsite.
    app.add_exception_handler(RateLimitExceeded, auth_rate_limit_handler)  # type: ignore[arg-type]

    install_problem_handlers(app)

    # Top-level health probe — kept off /v1 so monitors don't break across
    # major-version cuts.
    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(ai.router)
    app.include_router(admin.router)
    app.include_router(well_known_errors.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(households.router)
    app.include_router(accounts.router)
    app.include_router(transactions.router)
    app.include_router(periods.router)
    app.include_router(notifications.router)
    app.include_router(proposals.router)
    app.include_router(envelopes.router)
    app.include_router(sinking_funds.router)
    app.include_router(pools.router)
    app.include_router(refill_schedules.router)
    app.include_router(reports.router)
    app.include_router(journal.router)
    # csv_profiles must register BEFORE imports — both prefix on
    # /v1/imports, and the more specific /v1/imports/profiles router
    # must win route matching for the profile endpoints. (FastAPI
    # matches in registration order; including imports first would
    # absorb /v1/imports/profiles into the imports router.)
    app.include_router(csv_profiles.router)
    app.include_router(imports.router)
    app.include_router(reconciliations.router)

    return app
