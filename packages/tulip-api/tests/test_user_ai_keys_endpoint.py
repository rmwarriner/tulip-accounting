"""Tests for the per-user AI key endpoints (#239).

Per ADR-0005 §Q2: a user can upload their own provider key that takes
precedence over the household's for that user's AI calls. Endpoints are
``POST/DELETE/GET /v1/ai/keys/me/{provider}`` (self) and
``POST/DELETE/GET /v1/ai/keys/users/{user_id}/{provider}`` (admin-on-other).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


@pytest.fixture
def admin_h(client: TestClient) -> dict[str, str]:
    client.post(
        "/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
            "display_name": "Admin",
            "household_name": "Smith Family",
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestOwnKeyCrud:
    def test_set_then_list_then_delete_round_trips(
        self, client: TestClient, admin_h: dict[str, str]
    ) -> None:
        # Empty to start.
        r = client.get("/v1/ai/keys/me", headers=admin_h)
        assert r.status_code == 200
        assert r.json()["providers"] == []

        # Set anthropic key → 204.
        r = client.post(
            "/v1/ai/keys/me/anthropic",
            headers=admin_h,
            json={"api_key": "sk-user"},
        )
        assert r.status_code == 204, r.text

        # GET reflects it (but never returns the key value).
        r = client.get("/v1/ai/keys/me", headers=admin_h)
        assert r.json()["providers"] == ["anthropic"]
        assert "sk-user" not in r.text

        # Delete it → 204.
        r = client.delete("/v1/ai/keys/me/anthropic", headers=admin_h)
        assert r.status_code == 204

        # Empty again.
        assert client.get("/v1/ai/keys/me", headers=admin_h).json()["providers"] == []

    def test_set_audit_emits_provider_only_no_key_material(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        client.post(
            "/v1/ai/keys/me/anthropic",
            headers=admin_h,
            json={"api_key": "sk-secret-do-not-leak"},
        )

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "user.ai_key_set")
            ).scalar_one()
        assert row.metadata_ == {"provider": "anthropic"}
        # Key bytes never appear anywhere in the audit row.
        for blob in (row.before_snapshot, row.after_snapshot, row.metadata_):
            assert "sk-secret-do-not-leak" not in str(blob)

    def test_delete_is_idempotent(self, client: TestClient, admin_h: dict[str, str]) -> None:
        # Never set; delete returns 204 anyway.
        r = client.delete("/v1/ai/keys/me/anthropic", headers=admin_h)
        assert r.status_code == 204


class TestAdminKeyCrud:
    def test_admin_can_set_other_users_key(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            member_id = uuid4()
            s.add(
                User(
                    household_id=household.id,
                    id=member_id,
                    email="member@example.com",
                    password_hash=hash_password("memberpassword123"),
                    display_name="Member",
                    role=UserRole.MEMBER,
                )
            )
            s.commit()

        r = client.post(
            f"/v1/ai/keys/users/{member_id}/anthropic",
            headers=admin_h,
            json={"api_key": "sk-by-admin"},
        )
        assert r.status_code == 204, r.text

        r = client.get(f"/v1/ai/keys/users/{member_id}", headers=admin_h)
        assert r.json()["providers"] == ["anthropic"]

    def test_non_admin_cannot_set_others_key(
        self,
        client: TestClient,
        session_maker,
        admin_h: dict[str, str],  # ensures household is registered first
    ) -> None:
        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            target_id = uuid4()
            s.add_all(
                [
                    User(
                        household_id=household.id,
                        id=uuid4(),
                        email="member@example.com",
                        password_hash=hash_password("memberpassword123"),
                        display_name="Member",
                        role=UserRole.MEMBER,
                    ),
                    User(
                        household_id=household.id,
                        id=target_id,
                        email="target@example.com",
                        password_hash=hash_password("targetpassword123"),
                        display_name="Target",
                        role=UserRole.MEMBER,
                    ),
                ]
            )
            s.commit()

        login = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "memberpassword123"},
        )
        member_h = {"Authorization": f"Bearer {login.json()['access_token']}"}

        r = client.post(
            f"/v1/ai/keys/users/{target_id}/anthropic",
            headers=member_h,
            json={"api_key": "sk-evil"},
        )
        assert_problem(r, code="auth.forbidden", status=403)

    def test_admin_cannot_reach_other_household(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        client.post(
            "/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "other good password please",
                "display_name": "Other",
                "household_name": "Other Family",
            },
        )
        other_login = client.post(
            "/v1/auth/login",
            json={"email": "other@example.com", "password": "other good password please"},
        )
        other_h = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
        other_id = client.get("/v1/users/me/export", headers=other_h).json()["user"]["id"]

        r = client.post(
            f"/v1/ai/keys/users/{other_id}/anthropic",
            headers=admin_h,
            json={"api_key": "x"},
        )
        assert_problem(r, code="user.not_found", status=404)


class TestKeyAuth:
    def test_set_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.post("/v1/ai/keys/me/anthropic", json={"api_key": "x"})
        assert_problem(r, code="auth.unauthorized", status=401)

    def test_list_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.get("/v1/ai/keys/me")
        assert_problem(r, code="auth.unauthorized", status=401)
