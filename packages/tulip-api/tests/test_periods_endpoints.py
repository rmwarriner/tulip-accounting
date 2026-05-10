"""Tests for ``/v1/periods`` (#136 — ``tulip periods`` CLI surface)."""

from __future__ import annotations

from uuid import uuid4

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


def _seed_period_id(client: TestClient, auth_h: dict[str, str]) -> str:
    """Registration auto-seeds a current-year period; pluck its id."""
    r = client.get("/v1/periods", headers=auth_h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body, "expected the registration-seeded current-year period"
    return body[0]["id"]


class TestList:
    def test_lists_seeded_period(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.get("/v1/periods", headers=auth_h)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["status"] == "open"
        assert body[0]["closed_at"] is None
        assert body[0]["closed_by_user_id"] is None

    def test_unauth_returns_401(self, client: TestClient) -> None:
        r = client.get("/v1/periods")
        assert r.status_code == 401


class TestClose:
    def test_close_open_period(self, client: TestClient, auth_h: dict[str, str]) -> None:
        period_id = _seed_period_id(client, auth_h)
        r = client.post(f"/v1/periods/{period_id}/close", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "soft_closed"
        assert body["closed_at"] is not None
        assert body["closed_by_user_id"] is not None

    def test_close_is_idempotent(self, client: TestClient, auth_h: dict[str, str]) -> None:
        period_id = _seed_period_id(client, auth_h)
        first = client.post(f"/v1/periods/{period_id}/close", headers=auth_h)
        second = client.post(f"/v1/periods/{period_id}/close", headers=auth_h)
        assert second.status_code == 200
        # closed_at stamp doesn't move on the second call — idempotent no-op.
        # Strip any trailing 'Z'; SQLite + SQLAlchemy DateTime(timezone=True)
        # round-trips lose the tz offset on read, so the second response
        # serialises without it. Both refer to the same UTC instant.
        assert second.json()["closed_at"].rstrip("Z") == first.json()["closed_at"].rstrip("Z")

    def test_close_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(f"/v1/periods/{uuid4()}/close", headers=auth_h)
        assert_problem(r, code="period.not_found", status=404)

    def test_close_blocks_subsequent_writes(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Acceptance: close stamps the row so transaction writes 400 on it."""
        period_id = _seed_period_id(client, auth_h)
        client.post(f"/v1/periods/{period_id}/close", headers=auth_h).raise_for_status()

        # Build minimal account scaffolding to attempt a posting.
        a = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "1000", "name": "Cash", "type": "asset", "currency": "USD"},
        )
        a.raise_for_status()
        a_id = a.json()["id"]
        b = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5000", "name": "Food", "type": "expense", "currency": "USD"},
        )
        b.raise_for_status()
        b_id = b.json()["id"]

        # Pick a date inside the (now-closed) seeded period.
        from datetime import date

        today = date.today()
        tx = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date(today.year, 1, 15).isoformat(),
                "description": "Inside the closed period",
                "postings": [
                    {"account_id": a_id, "amount": "-12.34", "currency": "USD"},
                    {"account_id": b_id, "amount": "12.34", "currency": "USD"},
                ],
            },
        )
        assert_problem(tx, code="period.closed", status=400)


class TestReopen:
    def test_reopen_a_closed_period(self, client: TestClient, auth_h: dict[str, str]) -> None:
        period_id = _seed_period_id(client, auth_h)
        client.post(f"/v1/periods/{period_id}/close", headers=auth_h).raise_for_status()
        r = client.post(f"/v1/periods/{period_id}/reopen", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "open"
        assert body["closed_at"] is None
        assert body["closed_by_user_id"] is None

    def test_reopen_is_idempotent_on_already_open(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        period_id = _seed_period_id(client, auth_h)
        r = client.post(f"/v1/periods/{period_id}/reopen", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["status"] == "open"

    def test_reopen_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(f"/v1/periods/{uuid4()}/reopen", headers=auth_h)
        assert_problem(r, code="period.not_found", status=404)

    def test_round_trip_close_then_reopen_unblocks_writes(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        period_id = _seed_period_id(client, auth_h)
        client.post(f"/v1/periods/{period_id}/close", headers=auth_h).raise_for_status()
        client.post(f"/v1/periods/{period_id}/reopen", headers=auth_h).raise_for_status()

        # Seed accounts + post a transaction; should now succeed.
        a = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "1000", "name": "Cash", "type": "asset", "currency": "USD"},
        )
        a.raise_for_status()
        a_id = a.json()["id"]
        b = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"code": "5000", "name": "Food", "type": "expense", "currency": "USD"},
        )
        b.raise_for_status()
        b_id = b.json()["id"]

        from datetime import date

        today = date.today()
        tx = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date(today.year, 1, 15).isoformat(),
                "description": "After reopen",
                "postings": [
                    {"account_id": a_id, "amount": "-1.00", "currency": "USD"},
                    {"account_id": b_id, "amount": "1.00", "currency": "USD"},
                ],
            },
        )
        assert tx.status_code == 201, tx.text
