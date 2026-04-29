"""FastAPI app factory for Tulip Accounting.

`create_app()` builds the app with all routers, middleware, and lifespan
hooks attached. Tests construct fresh apps via this factory so each test
runs against an isolated, predictable instance.
"""

from __future__ import annotations

from fastapi import FastAPI

from tulip_api.routers import health

API_VERSION = "v1"
API_TITLE = "Tulip Accounting API"


def create_app() -> FastAPI:
    """Build a fresh FastAPI app instance."""
    app = FastAPI(
        title=API_TITLE,
        version="0.1.0",
        description=(
            "Household-focused double-entry accounting API. See ARCHITECTURE.md for design notes."
        ),
    )

    # Top-level health probe — kept off /v1 so monitors don't break across
    # major-version cuts.
    app.include_router(health.router)

    return app
