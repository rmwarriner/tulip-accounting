"""Tests for POST /v1/auth/password/change (issue #242 part C).

Verifies the caller's current password, updates the hash, and revokes all
of the user's outstanding refresh tokens — a stolen access token + a
forgotten refresh token shouldn't outlive the rotation. Audit row
``password_changed`` records the revocation count; password material
never enters the audit row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    body = {
        "email": "alice@example.com",
        "password": "correct horse battery staple",
        "display_name": "Alice",
        "household_name": "Smith Family",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


def _login(client: TestClient, body: dict[str, str]) -> dict:
    r = client.post(
        "/v1/auth/login",
        json={"email": body["email"], "password": body["password"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def access(client: TestClient, registered: dict[str, str]) -> str:
    return _login(client, registered)["access_token"]


@pytest.fixture
def auth_h(access: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access}"}


class TestPasswordChangeHappyPath:
    def test_password_change_returns_204(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
    ) -> None:
        r = client.post(
            "/v1/auth/password/change",
            headers=auth_h,
            json={
                "current_password": registered["password"],
                "new_password": "another good password please",
            },
        )
        assert r.status_code == 204, r.text
        assert r.content == b""

    def test_password_change_actually_rotates(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
    ) -> None:
        client.post(
            "/v1/auth/password/change",
            headers=auth_h,
            json={
                "current_password": registered["password"],
                "new_password": "another good password please",
            },
        )

        # Login with the new password works.
        ok = client.post(
            "/v1/auth/login",
            json={"email": registered["email"], "password": "another good password please"},
        )
        assert ok.status_code == 200

        # Login with the old password fails.
        bad = client.post(
            "/v1/auth/login",
            json={"email": registered["email"], "password": registered["password"]},
        )
        assert_problem(bad, code="auth.invalid_credentials", status=401)


class TestPasswordChangeRevokesSessions:
    def test_password_change_revokes_existing_refresh_tokens(
        self,
        client: TestClient,
        registered: dict[str, str],
    ) -> None:
        """Two prior login refresh tokens should both fail to refresh after a
        password change executed from a third login.
        """
        # Three logins ⇒ three refresh tokens.
        a = _login(client, registered)
        b = _login(client, registered)
        c = _login(client, registered)

        # Change password from session c.
        client.post(
            "/v1/auth/password/change",
            headers={"Authorization": f"Bearer {c['access_token']}"},
            json={
                "current_password": registered["password"],
                "new_password": "another good password please",
            },
        )

        # All three pre-change refresh tokens should be revoked.
        for tokens in (a, b, c):
            r = client.post(
                "/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            assert_problem(r, code="auth.invalid_refresh_token", status=401)


class TestPasswordChangeAudit:
    def test_password_change_writes_audit_row_with_no_password_material(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        # Add another active session so sessions_revoked > 1.
        _login(client, registered)

        client.post(
            "/v1/auth/password/change",
            headers=auth_h,
            json={
                "current_password": registered["password"],
                "new_password": "another good password please",
            },
        )

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "password_changed"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_type == "user"
        # Password material never enters the audit row.
        for blob in (row.before_snapshot, row.after_snapshot, row.metadata_):
            blob_str = str(blob) if blob is not None else ""
            assert registered["password"] not in blob_str
            assert "another good password please" not in blob_str
        # metadata records the revoke count.
        assert row.metadata_ is not None
        assert int(row.metadata_["sessions_revoked"]) >= 2


class TestPasswordChangeErrorPaths:
    def test_password_change_wrong_current_password_returns_401(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.post(
            "/v1/auth/password/change",
            headers=auth_h,
            json={
                "current_password": "wrong",
                "new_password": "another good password please",
            },
        )
        assert_problem(r, code="auth.invalid_credentials", status=401)

    def test_password_change_new_too_short_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
    ) -> None:
        r = client.post(
            "/v1/auth/password/change",
            headers=auth_h,
            json={
                "current_password": registered["password"],
                "new_password": "short",
            },
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_password_change_unauthenticated_returns_401(
        self,
        client: TestClient,
        registered: dict[str, str],
    ) -> None:
        r = client.post(
            "/v1/auth/password/change",
            json={
                "current_password": registered["password"],
                "new_password": "another good password please",
            },
        )
        assert_problem(r, code="auth.unauthorized", status=401)
