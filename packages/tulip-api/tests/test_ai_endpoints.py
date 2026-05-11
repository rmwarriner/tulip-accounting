"""Tests for ``/v1/ai/...`` endpoints (P6.1)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )
    return str(r.json()["access_token"])


@pytest.fixture
def auth_h(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


class TestKeys:
    def test_set_then_list_round_trip(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-test"})
        assert r.status_code == 204, r.text
        listing = client.get("/v1/ai/keys", headers=auth_h)
        assert listing.json()["providers"] == ["anthropic"]

    def test_keys_not_exposed_in_listing(self, client: TestClient, auth_h: dict[str, str]) -> None:
        """``list-keys`` returns provider names only — never the key bytes."""
        client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-very-secret"})
        listing = client.get("/v1/ai/keys", headers=auth_h)
        assert "sk-very-secret" not in listing.text

    def test_forget_key_removes_provider(self, client: TestClient, auth_h: dict[str, str]) -> None:
        client.post("/v1/ai/keys/anthropic", headers=auth_h, json={"api_key": "sk-test"})
        r = client.delete("/v1/ai/keys/anthropic", headers=auth_h)
        assert r.status_code == 204
        listing = client.get("/v1/ai/keys", headers=auth_h)
        assert listing.json()["providers"] == []

    def test_forget_unknown_provider_is_idempotent(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.delete("/v1/ai/keys/never-set", headers=auth_h)
        assert r.status_code == 204

    def test_keys_endpoints_require_admin(self, client: TestClient) -> None:
        r = client.post("/v1/ai/keys/anthropic", json={"api_key": "x"})
        assert r.status_code == 401


class TestStatus:
    def test_status_for_fresh_household_returns_defaults(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        body = client.get("/v1/ai/status", headers=auth_h).json()
        assert body["default_provider"] is None
        assert body["providers_with_keys"] == []
        # All four capabilities present.
        assert set(body["capabilities"].keys()) == {
            "categorize",
            "nl_query",
            "forecast",
            "agentic",
        }
        assert body["capabilities"]["categorize"]["level"] == "permissive"


class TestPreview:
    def test_preview_returns_byte_faithful_payload(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5100", "name": "Groceries", "type": "expense", "currency": "USD"},
        )
        client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5300", "name": "Fuel", "type": "expense", "currency": "USD"},
        )

        r = client.post(
            "/v1/ai/preview",
            headers=auth_h,
            json={
                "description": "WHOLE FOODS MARKET",
                "amount": "-87.42",
                "currency": "USD",
                "posted_date": "2026-05-03",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["profile"] == "default"
        payload = body["payload"]
        assert payload["task"] == "categorize"
        assert payload["line"]["description"] == "WHOLE FOODS MARKET"
        assert payload["line"]["amount"] == "-87.42"
        codes = sorted(c["code"] for c in payload["chart"])
        assert codes == ["5100", "5300"]

    def test_preview_requires_admin(self, client: TestClient) -> None:
        r = client.post(
            "/v1/ai/preview",
            json={
                "description": "X",
                "amount": "-1.00",
                "currency": "USD",
                "posted_date": "2026-05-03",
            },
        )
        assert r.status_code == 401
