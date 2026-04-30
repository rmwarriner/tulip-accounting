"""Tests for slice (c) — recovery codes (generate, redeem, regenerate, status)."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem
from tulip_storage.models import AuditLog, MfaRecoveryCode

REG_PASSWORD = "correct horse battery staple"


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    body = {
        "email": "alice@example.com",
        "password": REG_PASSWORD,
        "display_name": "Alice",
        "household_name": "Smith",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


def _login_access_token(client: TestClient, email: str = "alice@example.com") -> str:
    r = client.post("/v1/auth/login", json={"email": email, "password": REG_PASSWORD})
    return r.json()["access_token"]


def _enroll_through_verify(client: TestClient, access: str) -> tuple[str, list[str]]:
    """Enroll + verify; return (totp_secret, plaintext_recovery_codes)."""
    secret = client.post(
        "/v1/auth/mfa/enroll", headers={"Authorization": f"Bearer {access}"}
    ).json()["secret"]
    code = pyotp.TOTP(secret).now()
    verify = client.post(
        "/v1/auth/mfa/verify",
        headers={"Authorization": f"Bearer {access}"},
        json={"code": code},
    )
    assert verify.status_code == 200, verify.text
    return secret, verify.json()["recovery_codes"]


def _challenge_token(client: TestClient, email: str = "alice@example.com") -> str:
    """Drive the user past /login to get back an mfa_token."""
    return client.post("/v1/auth/login", json={"email": email, "password": REG_PASSWORD}).json()[
        "mfa_token"
    ]


class TestVerifyMintsCodes:
    def test_returns_eight_codes_in_xxxx_dash_xxxx_form(
        self, client: TestClient, registered: dict[str, str]
    ):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        assert len(codes) == 8
        assert len(set(codes)) == 8  # unique within the batch
        for c in codes:
            assert len(c) == 9 and c[4] == "-"

    def test_codes_are_stored_hashed_not_plaintext(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        access = _login_access_token(client)
        _, plaintext = _enroll_through_verify(client, access)
        with session_maker() as s:
            rows = s.execute(select(MfaRecoveryCode)).scalars().all()
        assert len(rows) == 8
        stored_hashes = {r.code_hash for r in rows}
        # No row's stored value matches any plaintext code.
        assert not (stored_hashes & set(plaintext))
        # Every row is argon2id-formatted (PHC string).
        for r in rows:
            assert r.code_hash.startswith("$argon2id$")
        # Nothing is consumed yet.
        assert all(r.used_at is None for r in rows)

    def test_audit_row_for_codes_generated(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        access = _login_access_token(client)
        _enroll_through_verify(client, access)
        with session_maker() as s:
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "mfa.recovery_codes_generated" in actions


class TestLoginRecover:
    def test_valid_code_issues_tokens(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        token = _challenge_token(client)

        r = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token, "recovery_code": codes[0]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] and body["refresh_token"]

    def test_used_code_cannot_be_reused(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)

        # First use → success.
        token1 = _challenge_token(client)
        first = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token1, "recovery_code": codes[0]},
        )
        assert first.status_code == 200, first.text

        # Second use of the same code, with a fresh challenge → must fail.
        token2 = _challenge_token(client)
        second = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token2, "recovery_code": codes[0]},
        )
        assert_problem(second, code="auth.mfa_invalid_recovery_code", status=401)

    def test_unknown_code_returns_problem(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _enroll_through_verify(client, access)
        token = _challenge_token(client)
        r = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token, "recovery_code": "ZZZZ-ZZZZ"},
        )
        assert_problem(r, code="auth.mfa_invalid_recovery_code", status=401)

    def test_input_normalization(self, client: TestClient, registered: dict[str, str]):
        # Codes returned in canonical XXXX-XXXX form must redeem under
        # alternate forms users typically transcribe (lowercase, no dash).
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        canonical = codes[0]
        no_dash = canonical.replace("-", "")
        token = _challenge_token(client)
        r = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token, "recovery_code": no_dash.lower()},
        )
        assert r.status_code == 200, r.text

    def test_garbage_token_rejected(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        r = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": "not-a-jwt", "recovery_code": codes[0]},
        )
        assert r.status_code == 401

    def test_audit_log_recovery_login(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        token = _challenge_token(client)
        client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token, "recovery_code": codes[0]},
        )
        with session_maker() as s:
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "mfa.recovery_login" in actions


class TestRegenerate:
    def test_requires_current_totp_code(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _enroll_through_verify(client, access)
        # New access token issued via /login/mfa would normally be required
        # for downstream calls. For the regenerate path we only need *some*
        # bearer with the right user, plus a current TOTP code in the body.
        r = client.post(
            "/v1/auth/mfa/recovery-codes/regenerate",
            headers={"Authorization": f"Bearer {access}"},
            json={"code": "000000"},  # wrong TOTP code
        )
        assert_problem(r, code="auth.mfa_invalid_code", status=401)

    def test_valid_totp_invalidates_old_codes_and_returns_eight_new(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        access = _login_access_token(client)
        secret, old_codes = _enroll_through_verify(client, access)
        old_set = set(old_codes)

        new_totp_code = pyotp.TOTP(secret).now()
        r = client.post(
            "/v1/auth/mfa/recovery-codes/regenerate",
            headers={"Authorization": f"Bearer {access}"},
            json={"code": new_totp_code},
        )
        assert r.status_code == 200, r.text
        new_codes = r.json()["recovery_codes"]
        assert len(new_codes) == 8
        # New batch is disjoint from old batch (overwhelmingly likely; the
        # birthday probability across 16 random 40-bit codes is negligible).
        assert not (set(new_codes) & old_set)

        # DB has exactly 8 rows; old ones are gone.
        with session_maker() as s:
            rows = s.execute(select(MfaRecoveryCode)).scalars().all()
        assert len(rows) == 8

    def test_old_codes_no_longer_redeem_after_regenerate(
        self, client: TestClient, registered: dict[str, str]
    ):
        access = _login_access_token(client)
        secret, old_codes = _enroll_through_verify(client, access)
        new_totp_code = pyotp.TOTP(secret).now()
        client.post(
            "/v1/auth/mfa/recovery-codes/regenerate",
            headers={"Authorization": f"Bearer {access}"},
            json={"code": new_totp_code},
        )
        # Old code can't be used to recover anymore.
        token = _challenge_token(client)
        r = client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token, "recovery_code": old_codes[0]},
        )
        assert_problem(r, code="auth.mfa_invalid_recovery_code", status=401)

    def test_regenerate_audit(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        access = _login_access_token(client)
        secret, _ = _enroll_through_verify(client, access)
        client.post(
            "/v1/auth/mfa/recovery-codes/regenerate",
            headers={"Authorization": f"Bearer {access}"},
            json={"code": pyotp.TOTP(secret).now()},
        )
        with session_maker() as s:
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "mfa.recovery_codes_regenerated" in actions


class TestStatus:
    def test_reports_eight_remaining_after_verify(
        self, client: TestClient, registered: dict[str, str]
    ):
        access = _login_access_token(client)
        _enroll_through_verify(client, access)
        r = client.get(
            "/v1/auth/mfa/recovery-codes/status",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"remaining": 8, "total": 8}

    def test_remaining_decrements_after_use(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        token = _challenge_token(client)
        client.post(
            "/v1/auth/login/recover",
            json={"mfa_token": token, "recovery_code": codes[0]},
        )
        # Use the new access token issued by /login/recover for the status call.
        new_access = client.post(
            "/v1/auth/login/recover",
            json={
                "mfa_token": _challenge_token(client),
                "recovery_code": codes[1],
            },
        ).json()["access_token"]
        r = client.get(
            "/v1/auth/mfa/recovery-codes/status",
            headers={"Authorization": f"Bearer {new_access}"},
        )
        assert r.json() == {"remaining": 6, "total": 8}

    def test_does_not_leak_codes(self, client: TestClient, registered: dict[str, str]):
        access = _login_access_token(client)
        _, codes = _enroll_through_verify(client, access)
        r = client.get(
            "/v1/auth/mfa/recovery-codes/status",
            headers={"Authorization": f"Bearer {access}"},
        )
        body = r.json()
        # Body must not contain any plaintext code or hash.
        assert "recovery_codes" not in body
        for c in codes:
            assert c not in r.text

    def test_unauthenticated(self, client: TestClient):
        r = client.get("/v1/auth/mfa/recovery-codes/status")
        assert r.status_code == 401
