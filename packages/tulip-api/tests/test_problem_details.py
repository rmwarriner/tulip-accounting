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

    def test_custom_headers_are_attached(self):
        client = _app_with_route(
            TulipProblem(
                code="example.unauth",
                title="Auth required",
                status=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        )
        r = client.get("/boom")
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        # Content-Type for the body must still be application/problem+json.
        assert r.headers["content-type"].startswith("application/problem+json")


class TestValidationErrorInputStripped:
    """#342 / privacy audit M-14: ``errors[N].input`` is removed from 422
    Problem Details responses to avoid echoing rejected request-body
    values (which may carry PII, secrets sent to the wrong endpoint, or
    arbitrary user input). The ``loc`` + ``msg`` + ``type`` triple
    identifies the failing field without re-emitting its value.
    """

    @staticmethod
    def _app_with_validated_body():
        from pydantic import BaseModel, Field

        class _Body(BaseModel):
            description: str = Field(max_length=10)

        app = FastAPI()
        install_problem_handlers(app)

        @app.post("/echo")
        def echo(body: _Body) -> dict[str, str]:
            return {"description": body.description}

        return TestClient(app)

    def test_long_string_input_is_not_echoed_in_response(self):
        client = self._app_with_validated_body()
        rejected = "A" * 500
        r = client.post("/echo", json={"description": rejected})
        assert r.status_code == 422
        body = r.json()
        # The rejected value must not appear anywhere in the response body.
        assert rejected not in r.text, "rejected request-body value must not echo"
        # The structured loc / msg / type triple is preserved.
        first_error = body["errors"][0]
        assert "loc" in first_error
        assert "msg" in first_error
        assert "type" in first_error
        # The ``input`` field must be stripped.
        assert "input" not in first_error

    def test_email_secret_value_is_not_echoed(self):
        """Scenario: a client accidentally posts a password where an email
        is expected. The rejected ``input`` value must not survive into
        the 422 response (it might land in proxy logs, browser dev-tools,
        etc.).
        """
        client = self._app_with_validated_body()
        secret = "this-is-not-a-description-it-is-a-secret"
        r = client.post("/echo", json={"description": secret})
        assert r.status_code == 422
        assert secret not in r.text
