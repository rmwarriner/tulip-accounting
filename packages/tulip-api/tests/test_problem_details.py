"""Tests for the Problem Details infrastructure (tulip_api.errors).

Exercises the exception base, the FastAPI exception handler, and the
shape of emitted ``application/problem+json`` responses, per
ARCHITECTURE §7.8.2.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from _problem_details import assert_problem
from tulip_api.errors import TulipProblem, install_problem_handlers


def _app_with_route(exc: Exception) -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return TestClient(app)


class TestTulipProblem:
    def test_minimum_shape(self):
        client = _app_with_route(
            TulipProblem(
                code="example.boom",
                title="Boom",
                status=418,
                detail="The teapot is boiling.",
            )
        )
        body = assert_problem(client.get("/boom"), code="example.boom", status=418, title="Boom")
        assert body["detail"] == "The teapot is boiling."
        assert body["instance"] == "/boom"
        # Default `type` URI is /.well-known/errors/<code> per §7.8.2.
        assert body["type"].endswith("/.well-known/errors/example.boom")

    def test_request_id_propagated_when_header_present(self):
        client = _app_with_route(TulipProblem(code="example.x", title="X", status=400, detail="x"))
        rid = "11111111-1111-1111-1111-111111111111"
        body = assert_problem(
            client.get("/boom", headers={"x-request-id": rid}),
            code="example.x",
            status=400,
        )
        assert body.get("request_id") == rid

    def test_extension_fields_appear_at_top_level(self):
        client = _app_with_route(
            TulipProblem(
                code="example.ext",
                title="Ext",
                status=429,
                detail="slow down",
                extensions={"retry_after_seconds": 30},
            )
        )
        body = assert_problem(client.get("/boom"), code="example.ext", status=429)
        assert body["retry_after_seconds"] == 30

    def test_detail_falls_back_to_title_when_omitted(self):
        client = _app_with_route(
            TulipProblem(code="example.terse", title="Just the title", status=400)
        )
        body = assert_problem(client.get("/boom"), code="example.terse", status=400)
        assert body["detail"] == "Just the title"
