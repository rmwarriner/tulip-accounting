"""Tests for structlog config + request_id middleware + PII redaction."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest
import structlog
from fastapi.testclient import TestClient

from tulip_api.logging_config import configure_logging, redact_pii
from tulip_api.main import create_app


@pytest.fixture
def captured_logs():
    """Capture structlog output as JSON dicts."""
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    prev_handlers = root.handlers[:]
    prev_level = root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    configure_logging()
    yield buf
    root.handlers = prev_handlers
    root.setLevel(prev_level)


class TestRequestIdMiddleware:
    def test_response_includes_x_request_id_header(self):
        client = TestClient(create_app())
        r = client.get("/health")
        assert "x-request-id" in {k.lower() for k in r.headers}
        # Value is a UUID-shaped string.
        rid = r.headers["x-request-id"]
        assert len(rid) == 36 and rid.count("-") == 4

    def test_caller_supplied_request_id_is_propagated(self):
        client = TestClient(create_app())
        rid = "11111111-2222-3333-4444-555555555555"
        r = client.get("/health", headers={"X-Request-Id": rid})
        assert r.headers["x-request-id"] == rid


class TestPIIRedaction:
    def test_redact_passes_unknown_fields_through(self):
        out = redact_pii(None, "info", {"event": "hello", "user": "alice"})
        assert out == {"event": "hello", "user": "alice"}

    @pytest.mark.parametrize(
        "field",
        [
            "password",
            "password_hash",
            "totp_secret",
            "totp_secret_encrypted",
            "api_key",
            "authorization",
            "external_account_number",
            "external_account_number_encrypted",
            "recovery_codes",
        ],
    )
    def test_redact_replaces_known_sensitive_fields(self, field: str):
        out = redact_pii(None, "info", {"event": "x", field: "secret-value-12345"})
        assert out[field] == "<redacted>"

    def test_redact_handles_nested_dicts(self):
        out = redact_pii(
            None,
            "info",
            {
                "event": "x",
                "payload": {"password": "p", "name": "alice"},
            },
        )
        assert out["payload"]["password"] == "<redacted>"
        assert out["payload"]["name"] == "alice"


class TestStructlogConfig:
    def test_logger_emits_json(self, captured_logs):
        log = structlog.get_logger("tulip_api.test")
        log.info("hello", foo="bar")
        line = captured_logs.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "hello"
        assert record["foo"] == "bar"
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_logger_redacts_password_field(self, captured_logs):
        log = structlog.get_logger("tulip_api.test")
        log.info("login", password="secret")
        line = captured_logs.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["password"] == "<redacted>"
