"""Tests for /v1/auth/mfa/{enroll,verify} (slice P2.x.1.a)."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem
from tulip_api.auth.mfa import decrypt_totp_secret
from tulip_storage.models import AuditLog, User

MASTER_KEY = b"\xab" * 32  # matches conftest.settings fixture


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Register + login a user; return Authorization headers."""
    client.post(
        "/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "correct horse battery staple",
            "display_name": "Alice",
            "household_name": "Smith",
        },
    )
    login = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    ).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


def _load_user(session_maker: sessionmaker[Session]) -> User:
    with session_maker() as s:
        return s.execute(select(User).where(User.email == "alice@example.com")).scalar_one()


class TestEnroll:
    def test_requires_auth(self, client: TestClient):
        # No Authorization header at all → 401 from get_current_claims.
        # That dependency still raises plain HTTPException; P2.x.2 will
        # migrate it. Here we only assert the status code, not the body.
        r = client.post("/v1/auth/mfa/enroll")
        assert r.status_code == 401

    def test_returns_secret_and_provisioning_uri(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        r = client.post("/v1/auth/mfa/enroll", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["secret"]
        assert body["provisioning_uri"].startswith("otpauth://totp/")
        assert body["secret"] in body["provisioning_uri"]

    def test_stores_encrypted_secret_with_enrolled_at_null(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        r = client.post("/v1/auth/mfa/enroll", headers=auth_headers)
        secret_returned = r.json()["secret"]

        user = _load_user(session_maker)
        assert user.totp_secret_encrypted is not None
        assert user.totp_enrolled_at is None
        # Stored blob round-trips back to the secret returned to the user.
        assert (
            decrypt_totp_secret(user.totp_secret_encrypted, master_key=MASTER_KEY)
            == secret_returned
        )

    def test_re_enroll_before_verify_rotates_secret(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        first = client.post("/v1/auth/mfa/enroll", headers=auth_headers).json()
        second = client.post("/v1/auth/mfa/enroll", headers=auth_headers).json()
        assert first["secret"] != second["secret"]
        # Stored blob matches the most recent enrollment.
        user = _load_user(session_maker)
        assert (
            decrypt_totp_secret(user.totp_secret_encrypted, master_key=MASTER_KEY)
            == second["secret"]
        )

    def test_re_enroll_after_verify_rejected(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        # Complete the enrollment first.
        secret = client.post("/v1/auth/mfa/enroll", headers=auth_headers).json()["secret"]
        code = pyotp.TOTP(secret).now()
        verify = client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": code})
        assert verify.status_code == 204, verify.text

        # Re-enroll → 409 with mfa_already_enrolled.
        r = client.post("/v1/auth/mfa/enroll", headers=auth_headers)
        assert_problem(r, code="auth.mfa_already_enrolled", status=409)

    def test_audit_log_written(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        client.post("/v1/auth/mfa/enroll", headers=auth_headers)
        with session_maker() as s:
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "mfa.enroll" in actions


class TestVerify:
    def test_requires_auth(self, client: TestClient):
        r = client.post("/v1/auth/mfa/verify", json={"code": "123456"})
        assert r.status_code == 401

    def test_no_pending_enrollment_returns_problem(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        r = client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": "123456"})
        assert_problem(r, code="auth.mfa_not_pending", status=400)

    def test_wrong_code_returns_problem(self, client: TestClient, auth_headers: dict[str, str]):
        client.post("/v1/auth/mfa/enroll", headers=auth_headers)
        r = client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": "000000"})
        assert_problem(r, code="auth.mfa_invalid_code", status=401)

    def test_correct_code_returns_204_and_marks_enrolled(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        secret = client.post("/v1/auth/mfa/enroll", headers=auth_headers).json()["secret"]
        code = pyotp.TOTP(secret).now()
        r = client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": code})
        assert r.status_code == 204
        user = _load_user(session_maker)
        assert user.totp_enrolled_at is not None

    def test_already_verified_returns_problem(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        secret = client.post("/v1/auth/mfa/enroll", headers=auth_headers).json()["secret"]
        code = pyotp.TOTP(secret).now()
        client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": code})
        # Second verify call → 409.
        r = client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": code})
        assert_problem(r, code="auth.mfa_already_enrolled", status=409)

    def test_audit_log_written_on_success(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        secret = client.post("/v1/auth/mfa/enroll", headers=auth_headers).json()["secret"]
        code = pyotp.TOTP(secret).now()
        client.post("/v1/auth/mfa/verify", headers=auth_headers, json={"code": code})
        with session_maker() as s:
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "mfa.verify" in actions
