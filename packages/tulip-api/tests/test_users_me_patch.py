"""Tests for PATCH /v1/users/me — GDPR Art. 16 profile rectification (issue #242).

Mutations:
* ``display_name`` may be updated without re-auth.
* ``email`` is the login identifier, so changing it requires the caller
  to re-submit their ``current_password`` in the body.

Audit row: ``profile_updated`` with ``before_snapshot`` / ``after_snapshot``
recording only the keys actually changed.
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


@pytest.fixture
def auth_h(client: TestClient, registered: dict[str, str]) -> dict[str, str]:
    r = client.post(
        "/v1/auth/login",
        json={"email": registered["email"], "password": registered["password"]},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestPatchProfileHappyPath:
    def test_patch_display_name_without_reauth(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={"display_name": "Alice Smith"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["display_name"] == "Alice Smith"
        # Email round-trips unchanged.
        assert body["email"] == "alice@example.com"

    def test_patch_email_with_correct_password_updates(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
    ) -> None:
        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={
                "email": "alice2@example.com",
                "current_password": registered["password"],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "alice2@example.com"

        # New email works for login.
        ok = client.post(
            "/v1/auth/login",
            json={"email": "alice2@example.com", "password": registered["password"]},
        )
        assert ok.status_code == 200

        # Old email fails.
        bad = client.post(
            "/v1/auth/login",
            json={"email": "alice@example.com", "password": registered["password"]},
        )
        assert_problem(bad, code="auth.invalid_credentials", status=401)

    def test_patch_email_and_display_name_in_one_request(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
    ) -> None:
        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={
                "display_name": "Renamed",
                "email": "renamed@example.com",
                "current_password": registered["password"],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == "renamed@example.com"
        assert body["display_name"] == "Renamed"


class TestPatchProfileAudit:
    def test_patch_writes_profile_updated_audit_row(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={
                "display_name": "Renamed",
                "email": "renamed@example.com",
                "current_password": registered["password"],
            },
        )

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "profile_updated"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_type == "user"
        assert row.before_snapshot is not None
        assert row.before_snapshot["display_name"] == "Alice"
        assert row.before_snapshot["email"] == "alice@example.com"
        assert row.after_snapshot is not None
        assert row.after_snapshot["display_name"] == "Renamed"
        assert row.after_snapshot["email"] == "renamed@example.com"

    def test_patch_display_name_only_omits_email_from_snapshots(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={"display_name": "Renamed"},
        )

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "profile_updated")
            ).scalar_one()
        assert "display_name" in row.before_snapshot
        assert "email" not in row.before_snapshot
        assert "display_name" in row.after_snapshot
        assert "email" not in row.after_snapshot


class TestPatchProfileErrorPaths:
    def test_patch_email_without_current_password_returns_401(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={"email": "alice2@example.com"},
        )
        assert_problem(r, code="auth.reauth_required", status=401)

    def test_patch_email_with_wrong_password_returns_401(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={"email": "alice2@example.com", "current_password": "wrong"},
        )
        assert_problem(r, code="auth.invalid_credentials", status=401)

    def test_patch_email_duplicate_within_household_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        registered: dict[str, str],
        session_maker,
    ) -> None:
        """Two users in the same household with the same email is forbidden
        by the (household_id, email) unique constraint."""
        from uuid import uuid4

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import User, UserRole

        # Create a sibling user in the same household via the DB.
        with session_maker() as s:
            from sqlalchemy import select

            existing = s.execute(select(User).where(User.email == registered["email"])).scalar_one()
            sibling = User(
                household_id=existing.household_id,
                id=uuid4(),
                email="sibling@example.com",
                password_hash=hash_password("anyother goodpass"),
                display_name="Sibling",
                role=UserRole.MEMBER,
            )
            s.add(sibling)
            s.commit()

        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={
                "email": "sibling@example.com",
                "current_password": registered["password"],
            },
        )
        assert_problem(r, code="auth.duplicate_email", status=409)

    def test_patch_empty_body_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.patch("/v1/users/me", headers=auth_h, json={})
        assert_problem(r, code="validation.failed", status=422)

    def test_patch_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.patch("/v1/users/me", json={"display_name": "x"})
        assert_problem(r, code="auth.unauthorized", status=401)

    def test_patch_display_name_too_long_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        r = client.patch(
            "/v1/users/me",
            headers=auth_h,
            json={"display_name": "x" * 201},
        )
        assert_problem(r, code="validation.failed", status=422)
