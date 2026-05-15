"""Tests for structlog config + request_id middleware + PII redaction."""

from __future__ import annotations

import json

import pytest
import structlog
from fastapi.testclient import TestClient

from tulip_api.logging_config import configure_logging, redact_pii
from tulip_api.main import create_app


@pytest.fixture
def configured_logging():
    """Configure structlog for the test (idempotent across tests)."""
    configure_logging()
    yield


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
            # H-5 (#220): emails are personal data per GDPR.
            "email",
            "user_email",
            # M-2 (#246): IP + user-agent are personal data (Recital 30).
            "ip_address",
            "user_agent",
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
    def test_logger_emits_json(self, configured_logging, capsys):
        log = structlog.get_logger("tulip_api.test")
        log.info("hello", foo="bar")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "hello"
        assert record["foo"] == "bar"
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_logger_redacts_password_field(self, configured_logging, capsys):
        log = structlog.get_logger("tulip_api.test")
        log.info("login", password="secret")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        record = json.loads(line)
        assert record["password"] == "<redacted>"


class TestStdlibFilter:
    """#220 (H-6): stdlib `logging` calls with `extra={...}` must redact too.

    Before the filter, `tulip_api.config`, dependency SDKs, and any
    third-party stdlib caller bypassed the structlog whitelist entirely.
    """

    def test_filter_redacts_extra_password(self, configured_logging, caplog):
        import logging as _logging

        caplog.set_level(_logging.INFO)
        log = _logging.getLogger("tulip_api.test_filter")
        log.info("login.attempt", extra={"password": "secret"})
        # caplog captures the resolved record (after filter runs).
        record = caplog.records[-1]
        assert record.password == "<redacted>"

    def test_filter_redacts_extra_email(self, configured_logging, caplog):
        import logging as _logging

        caplog.set_level(_logging.INFO)
        log = _logging.getLogger("tulip_api.test_filter")
        log.info("login.failed", extra={"email": "alice@example.com"})
        record = caplog.records[-1]
        assert record.email == "<redacted>"

    def test_filter_redacts_extra_ip_address(self, configured_logging, caplog):
        """#246 (M-2): GDPR Recital 30 — IPs are personal data."""
        import logging as _logging

        caplog.set_level(_logging.INFO)
        log = _logging.getLogger("tulip_api.test_filter")
        log.info("login.attempt", extra={"ip_address": "203.0.113.7"})
        record = caplog.records[-1]
        assert record.ip_address == "<redacted>"

    def test_filter_redacts_extra_user_agent(self, configured_logging, caplog):
        """#246 (M-2): user-agent fingerprints the caller and is personal data."""
        import logging as _logging

        caplog.set_level(_logging.INFO)
        log = _logging.getLogger("tulip_api.test_filter")
        log.info("login.attempt", extra={"user_agent": "Mozilla/5.0 ..."})
        record = caplog.records[-1]
        assert record.user_agent == "<redacted>"

    def test_filter_leaves_unknown_fields_alone(self, configured_logging, caplog):
        import logging as _logging

        caplog.set_level(_logging.INFO)
        log = _logging.getLogger("tulip_api.test_filter")
        log.info("event", extra={"foo": "bar"})
        record = caplog.records[-1]
        assert record.foo == "bar"

    def test_redactor_is_idempotent_under_repeat_configure(self):
        import logging as _logging

        from tulip_api.logging_config import (
            _REDACTOR_INSTALLED_MARKER,
            configure_logging,
        )

        configure_logging()
        configure_logging()
        configure_logging()
        # `Logger.makeRecord` carries the marker; it stays a single wrap
        # regardless of how many times we configure.
        assert getattr(_logging.Logger.makeRecord, _REDACTOR_INSTALLED_MARKER, False)
