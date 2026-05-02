"""Tests for /v1/auth/{register,login,refresh,logout}."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    """Register a household + admin user; return the auth payload."""
    body = {
        "email": "alice@example.com",
        "password": "correct horse battery staple",
        "display_name": "Alice",
        "household_name": "Smith Family",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


class TestRegister:
    def test_creates_household_and_admin_user(self, client: TestClient):
        r = client.post(
            "/v1/auth/register",
            json={
                "email": "alice@example.com",
                "password": "correct horse battery staple",
                "display_name": "Alice",
                "household_name": "Smith",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert "user_id" in body
        assert "household_id" in body
        assert body["role"] == "admin"

    def test_duplicate_email_in_separate_households_succeeds(
        self, client: TestClient, registered: dict[str, str]
    ):
        # Register always creates a NEW household, so the same email
        # appearing in two different households is fine — the unique
        # constraint is (household_id, email) per ARCHITECTURE §4.1.
        r = client.post(
            "/v1/auth/register",
            json={
                "email": registered["email"],
                "password": "different-password-123",
                "display_name": "Other",
                "household_name": "Other Family",
            },
        )
        assert r.status_code == 201

    def test_password_too_short_rejected(self, client: TestClient):
        r = client.post(
            "/v1/auth/register",
            json={
                "email": "x@y.z",
                "password": "short",
                "display_name": "X",
                "household_name": "X",
            },
        )
        assert r.status_code == 422

    def test_register_seeds_system_pools_for_base_currency(self, client, session_maker):
        # ADR-0001: Inflow / Unallocated / Spent per (household, currency)
        # are auto-created at household creation for the base currency.
        r = client.post(
            "/v1/auth/register",
            json={
                "email": "alice@example.com",
                "password": "correct horse battery staple",
                "display_name": "Alice",
                "household_name": "Smith",
            },
        )
        assert r.status_code == 201
        household_id = r.json()["household_id"]

        from uuid import UUID

        from tulip_storage.models import AllocationPool, PoolType

        with session_maker() as s:
            from sqlalchemy import select

            rows = list(
                s.execute(
                    select(AllocationPool).where(
                        AllocationPool.household_id == UUID(household_id),
                        AllocationPool.is_system.is_(True),
                    )
                )
                .scalars()
                .all()
            )
            types = {p.pool_type for p in rows}
            assert types == {PoolType.INFLOW, PoolType.UNALLOCATED, PoolType.SPENT}
            assert all(p.currency == "USD" for p in rows)
            assert all(p.is_active for p in rows)


class TestLogin:
    def test_returns_access_and_refresh(self, client: TestClient, registered: dict[str, str]):
        r = client.post(
            "/v1/auth/login",
            json={"email": registered["email"], "password": registered["password"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["access_token"] != body["refresh_token"]

    def test_wrong_password_returns_invalid_credentials(
        self, client: TestClient, registered: dict[str, str]
    ):
        r = client.post(
            "/v1/auth/login",
            json={"email": registered["email"], "password": "wrong"},
        )
        body = assert_problem(r, code="auth.invalid_credentials", status=401)
        # Identical body for unknown-email case below — no oracle.
        assert "password" not in body["detail"].lower() or "email" in body["detail"].lower()
        assert r.headers["www-authenticate"] == "Bearer"

    def test_unknown_email_returns_invalid_credentials(self, client: TestClient):
        r = client.post(
            "/v1/auth/login",
            json={"email": "ghost@example.com", "password": "whatever"},
        )
        assert_problem(r, code="auth.invalid_credentials", status=401)
        assert r.headers["www-authenticate"] == "Bearer"


class TestRefresh:
    def test_rotates_refresh_token(self, client: TestClient, registered: dict[str, str]):
        login = client.post(
            "/v1/auth/login",
            json={"email": registered["email"], "password": registered["password"]},
        ).json()
        old_refresh = login["refresh_token"]

        r = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert r.status_code == 200
        body = r.json()
        assert body["access_token"] and body["refresh_token"]
        # Refresh tokens rotate — same one cannot be reused.
        r2 = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert_problem(r2, code="auth.invalid_refresh_token", status=401)

    def test_unknown_refresh_token_rejected(self, client: TestClient):
        r = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": "not-a-real-token"},
        )
        assert_problem(r, code="auth.invalid_refresh_token", status=401)


class TestLogout:
    def test_revokes_refresh_token(self, client: TestClient, registered: dict[str, str]):
        login = client.post(
            "/v1/auth/login",
            json={"email": registered["email"], "password": registered["password"]},
        ).json()
        rt = login["refresh_token"]

        r = client.post("/v1/auth/logout", json={"refresh_token": rt})
        assert r.status_code == 204

        # Subsequent refresh with the same token must be rejected.
        r2 = client.post("/v1/auth/refresh", json={"refresh_token": rt})
        assert_problem(r2, code="auth.invalid_refresh_token", status=401)
