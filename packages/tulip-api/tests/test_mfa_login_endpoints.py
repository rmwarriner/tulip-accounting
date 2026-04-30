"""Tests for the login challenge gate (P2.x.1.b).

Covers /v1/auth/login outcomes when MFA is involved, plus the new
/v1/auth/login/mfa step-2 endpoint.
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem
from tulip_storage.models import AuditLog, Household, MfaPolicy, User, UserRole

REG_PASSWORD = "correct horse battery staple"


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    """Register a household + admin user and return the request body."""
    body = {
        "email": "alice@example.com",
        "password": REG_PASSWORD,
        "display_name": "Alice",
        "household_name": "Smith",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


def _login(client: TestClient, email: str, password: str = REG_PASSWORD):
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def _enroll_and_verify(client: TestClient, access_token: str) -> str:
    """Walk a user through the slice (a) flow; return the base32 secret."""
    secret = client.post(
        "/v1/auth/mfa/enroll", headers={"Authorization": f"Bearer {access_token}"}
    ).json()["secret"]
    code = pyotp.TOTP(secret).now()
    r = client.post(
        "/v1/auth/mfa/verify",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"code": code},
    )
    assert r.status_code == 204, r.text
    return secret


def _set_household_mfa_policy(session_maker: sessionmaker[Session], policy: MfaPolicy) -> None:
    with session_maker() as s:
        h = s.execute(select(Household)).scalar_one()
        h.mfa_policy = policy
        s.commit()


def _set_user_role(session_maker: sessionmaker[Session], email: str, role: UserRole) -> None:
    with session_maker() as s:
        u = s.execute(select(User).where(User.email == email)).scalar_one()
        u.role = role
        s.commit()


class TestLoginNoMfa:
    def test_unenrolled_optional_policy_issues_tokens(
        self, client: TestClient, registered: dict[str, str]
    ):
        # Default policy = OPTIONAL, default enrollment = none → behaves
        # exactly like before P2.x.1.b.
        r = _login(client, registered["email"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] and body["refresh_token"]


class TestLoginEnrolledChallenges:
    def test_enrolled_user_gets_mfa_required(self, client: TestClient, registered: dict[str, str]):
        access = _login(client, registered["email"]).json()["access_token"]
        _enroll_and_verify(client, access)

        # Step-1 login now must challenge.
        r = _login(client, registered["email"])
        body = assert_problem(r, code="auth.mfa_required", status=401)
        # Flat top-level extensions per design decision (3).
        assert body["mfa_token"], "mfa_token extension missing"
        assert body["mfa_token_expires_in"] > 0
        # Critically, no tokens leaked.
        assert "access_token" not in body
        assert "refresh_token" not in body

    def test_wrong_password_for_enrolled_user_returns_invalid_credentials(
        self, client: TestClient, registered: dict[str, str]
    ):
        access = _login(client, registered["email"]).json()["access_token"]
        _enroll_and_verify(client, access)

        # Wrong password must NOT leak whether the account is enrolled.
        r = _login(client, registered["email"], password="wrong")
        assert r.status_code == 401
        # The body must not say "mfa_required" — that would oracle the
        # account's enrollment state to an unauthenticated attacker.
        body_text = r.text.lower()
        assert "mfa_required" not in body_text


class TestLoginEnforcesPolicy:
    def test_admin_with_required_for_admins_must_enroll(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        # Registered user is admin by default; ratchet policy.
        _set_household_mfa_policy(session_maker, MfaPolicy.REQUIRED_FOR_ADMINS)

        r = _login(client, registered["email"])
        body = assert_problem(r, code="auth.mfa_enrollment_required", status=403)
        assert body["enrollment_url"] == "/v1/auth/mfa/enroll"

    def test_member_with_required_for_admins_passes(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        # Demote the registered user, then turn on the admins-only policy.
        _set_user_role(session_maker, registered["email"], UserRole.MEMBER)
        _set_household_mfa_policy(session_maker, MfaPolicy.REQUIRED_FOR_ADMINS)

        r = _login(client, registered["email"])
        assert r.status_code == 200, r.text
        assert r.json()["access_token"]

    def test_anyone_with_required_for_all_must_enroll(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        _set_user_role(session_maker, registered["email"], UserRole.MEMBER)
        _set_household_mfa_policy(session_maker, MfaPolicy.REQUIRED_FOR_ALL)

        r = _login(client, registered["email"])
        assert_problem(r, code="auth.mfa_enrollment_required", status=403)


class TestLoginMfaCompletion:
    def _challenge(self, client: TestClient, registered: dict[str, str]) -> tuple[str, str]:
        """Drive the user to enrolled state and capture (mfa_token, secret)."""
        access = _login(client, registered["email"]).json()["access_token"]
        secret = _enroll_and_verify(client, access)
        challenge = _login(client, registered["email"]).json()
        return challenge["mfa_token"], secret

    def test_valid_token_and_code_issues_tokens(
        self, client: TestClient, registered: dict[str, str]
    ):
        mfa_token, secret = self._challenge(client, registered)
        code = pyotp.TOTP(secret).now()
        r = client.post("/v1/auth/login/mfa", json={"mfa_token": mfa_token, "code": code})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "Bearer"

    def test_audit_log_login_mfa_success(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        mfa_token, secret = self._challenge(client, registered)
        code = pyotp.TOTP(secret).now()
        client.post("/v1/auth/login/mfa", json={"mfa_token": mfa_token, "code": code})

        with session_maker() as s:
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "login_mfa_success" in actions

    def test_wrong_code_returns_invalid_code(self, client: TestClient, registered: dict[str, str]):
        mfa_token, _ = self._challenge(client, registered)
        r = client.post("/v1/auth/login/mfa", json={"mfa_token": mfa_token, "code": "000000"})
        assert_problem(r, code="auth.mfa_invalid_code", status=401)

    def test_garbage_mfa_token_rejected(self, client: TestClient, registered: dict[str, str]):
        # Need a valid TOTP code so we know the rejection is on the token,
        # not the code.
        _mfa_token, secret = self._challenge(client, registered)
        code = pyotp.TOTP(secret).now()
        r = client.post("/v1/auth/login/mfa", json={"mfa_token": "not-a-real-jwt", "code": code})
        assert r.status_code == 401

    def test_access_token_rejected_as_mfa_token(
        self, client: TestClient, registered: dict[str, str]
    ):
        # An attacker who steals an access token must NOT be able to
        # short-circuit MFA by passing it in here.
        access = _login(client, registered["email"]).json()["access_token"]
        secret = _enroll_and_verify(client, access)
        code = pyotp.TOTP(secret).now()
        r = client.post("/v1/auth/login/mfa", json={"mfa_token": access, "code": code})
        assert r.status_code == 401
