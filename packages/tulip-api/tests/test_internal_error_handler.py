"""Tests for the catch-all unhandled-exception handler.

`tulip_api.errors.install_problem_handlers` registers handlers for
``TulipProblem`` (typed errors), ``RequestValidationError`` (Pydantic
422), and Starlette's ``HTTPException`` (framework 400/404/405). This
slice (#26) closes the last gap: an unhandled exception escaping a
route handler must also produce ``application/problem+json``, not the
``text/plain`` 500 Starlette emits by default.

These tests use a dedicated FastAPI app with deliberate-panic routes
rather than the production app — schemathesis asserts that documented
responses match what comes back, and 500 is intentionally **not**
declared on production routes (it's an "unexpected" path that should
never be a normal client response).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tulip_api.errors import PROBLEM_CONTENT_TYPE, install_problem_handlers


@pytest.fixture
def panic_client() -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/boom-runtime")
    def _boom_runtime() -> dict[str, str]:
        raise RuntimeError("the secret detail nobody should see")

    @app.get("/boom-value")
    def _boom_value() -> dict[str, str]:
        raise ValueError("another internal hint")

    @app.get("/boom-key")
    def _boom_key() -> dict[str, str]:
        d: dict[str, str] = {}
        return {"x": d["missing"]}

    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_runtime_error_renders_problem_json(panic_client: TestClient) -> None:
    response = panic_client.get("/boom-runtime")
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    assert body["status"] == 500
    assert body["code"] == "server.internal_error"
    assert body["title"]
    assert body["detail"]


def test_internal_error_does_not_leak_exception_text(panic_client: TestClient) -> None:
    response = panic_client.get("/boom-runtime")
    raw = response.text
    # The exception message must not appear in the body — that's the
    # whole point of the handler.
    assert "the secret detail" not in raw
    assert "RuntimeError" not in raw
    assert "Traceback" not in raw


def test_internal_error_handler_works_for_other_exception_types(
    panic_client: TestClient,
) -> None:
    """Catch-all means catch-all — ValueError, KeyError, anything."""
    for path in ("/boom-value", "/boom-key"):
        response = panic_client.get(path)
        assert response.status_code == 500
        assert response.json()["code"] == "server.internal_error"


def test_internal_error_carries_request_id_when_supplied_by_client(
    panic_client: TestClient,
) -> None:
    """A client-supplied X-Request-Id is echoed in the body so support tickets reference it."""
    response = panic_client.get(
        "/boom-runtime",
        headers={"x-request-id": "00000000-0000-0000-0000-000000000abc"},
    )
    assert response.json().get("request_id") == "00000000-0000-0000-0000-000000000abc"


def test_typed_problem_handler_still_wins_over_catchall(panic_client: TestClient) -> None:
    """Registering an Exception handler must not shadow the TulipProblem handler.

    Starlette dispatches exception handlers by MRO, picking the most
    specific match — but verify that explicitly. A TulipProblem subclass
    must still render with its own status / code, not the 500 wrapper.
    """
    from tulip_api.errors import TulipProblem

    app = FastAPI()
    install_problem_handlers(app)

    class TypedFailure(TulipProblem):
        def __init__(self) -> None:
            super().__init__(code="x.typed", title="Typed", status=409, detail="typed detail")

    @app.get("/typed")
    def _typed() -> None:
        raise TypedFailure()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/typed")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "x.typed"
    assert body["title"] == "Typed"
