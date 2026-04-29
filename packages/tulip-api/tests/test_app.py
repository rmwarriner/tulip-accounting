"""Smoke tests for the FastAPI app factory."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tulip_api.main import create_app


def test_health_endpoint_returns_200():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_openapi_spec_is_served():
    client = TestClient(create_app())
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "Tulip Accounting API"


def test_v1_prefix_is_used():
    """All non-meta routes are under /v1; root /health and /openapi are top-level."""
    app = create_app()
    paths = {route.path for route in app.routes}
    business_paths = paths - {
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
    }
    for path in business_paths:
        if path.startswith("/v") or path.startswith("/.well-known"):
            continue
        # Allow /openapi.json variants and /docs etc; otherwise must be under /v1.
        assert path.startswith("/v1") or path.startswith("/.well-known"), (
            f"non-versioned business route: {path}"
        )
