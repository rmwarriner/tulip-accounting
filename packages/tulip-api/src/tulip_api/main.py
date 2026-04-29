"""FastAPI app factory for Tulip Accounting.

`create_app()` builds the app with all routers, middleware, and lifespan
hooks attached. Tests construct fresh apps via this factory so each test
runs against an isolated, predictable instance.
"""

from __future__ import annotations

from fastapi import FastAPI

from tulip_api.logging_config import configure_logging
from tulip_api.middleware import RequestIdMiddleware
from tulip_api.routers import accounts, auth, health, transactions

API_VERSION = "v1"
API_TITLE = "Tulip Accounting API"


def create_app() -> FastAPI:
    """Build a fresh FastAPI app instance."""
    configure_logging()

    app = FastAPI(
        title=API_TITLE,
        version="0.1.0",
        description=(
            "Household-focused double-entry accounting API. See ARCHITECTURE.md for design notes."
        ),
    )

    # Request-id stamping must run before any router-level logging so the
    # request_id is in scope for every log line emitted during handling.
    app.add_middleware(RequestIdMiddleware)

    # Top-level health probe — kept off /v1 so monitors don't break across
    # major-version cuts.
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(transactions.router)

    return app
