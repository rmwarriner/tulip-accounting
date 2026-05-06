"""Tests for /v1/accounts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


@pytest.fixture
def admin_token(client: TestClient) -> str:
    client.post(
        "/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
            "display_name": "Admin",
            "household_name": "Smith",
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
    )
    return r.json()["access_token"]


@pytest.fixture
def auth_h(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


class TestAuthGuards:
    def test_no_token_returns_unauthorized(self, client: TestClient):
        r = client.get("/v1/accounts")
        assert_problem(r, code="auth.unauthorized", status=401)
        assert r.headers["www-authenticate"] == "Bearer"

    def test_garbage_token_returns_unauthorized(self, client: TestClient):
        r = client.get("/v1/accounts", headers={"Authorization": "Bearer xxx"})
        assert_problem(r, code="auth.unauthorized", status=401)


class TestAccountCrud:
    def test_create_and_list(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "1110",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Checking"
        assert body["type"] == "asset"
        assert body["is_active"] is True

        r2 = client.get("/v1/accounts", headers=auth_h)
        assert r2.status_code == 200
        rows = r2.json()
        # Registration seeds Imbalance:Unknown (P5.4.a) — filter to the
        # account we just created to keep the assertion intent-focused.
        names = {row["name"] for row in rows}
        assert "Checking" in names

    def test_get_returns_not_found_for_unknown(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get(
            "/v1/accounts/00000000-0000-0000-0000-000000000000",
            headers=auth_h,
        )
        assert_problem(r, code="account.not_found", status=404)

    def test_update(self, client: TestClient, auth_h: dict[str, str]):
        a = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Old", "type": "asset", "currency": "USD"},
        ).json()
        r = client.patch(
            f"/v1/accounts/{a['id']}",
            headers=auth_h,
            json={"name": "New"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "New"

    def test_delete_deactivates(self, client: TestClient, auth_h: dict[str, str]):
        a = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Bye", "type": "asset", "currency": "USD"},
        ).json()
        r = client.delete(f"/v1/accounts/{a['id']}", headers=auth_h)
        assert r.status_code == 204

        # No longer listed.
        rows = client.get("/v1/accounts", headers=auth_h).json()
        assert all(row["id"] != a["id"] for row in rows)

    def test_validation_rejects_unknown_type(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "X", "type": "money", "currency": "USD"},
        )
        assert r.status_code == 422


class TestTenantIsolation:
    def test_two_households_dont_see_each_others_accounts(self, client: TestClient):
        # Register two households via separate sessions.
        for email, name in [("a@x.com", "A"), ("b@y.com", "B")]:
            client.post(
                "/v1/auth/register",
                json={
                    "email": email,
                    "password": "correct horse battery staple",
                    "display_name": email,
                    "household_name": name,
                },
            )
        a_token = client.post(
            "/v1/auth/login",
            json={"email": "a@x.com", "password": "correct horse battery staple"},
        ).json()["access_token"]
        b_token = client.post(
            "/v1/auth/login",
            json={"email": "b@y.com", "password": "correct horse battery staple"},
        ).json()["access_token"]

        client.post(
            "/v1/accounts",
            headers={"Authorization": f"Bearer {a_token}"},
            json={"name": "A's account", "type": "asset", "currency": "USD"},
        )
        rows = client.get("/v1/accounts", headers={"Authorization": f"Bearer {b_token}"}).json()
        # Household B sees its own seeded Imbalance:Unknown (P5.4.a) but
        # not the account A just created.
        names = {row["name"] for row in rows}
        assert "A's account" not in names
        assert names <= {"Imbalance: Unknown"}
