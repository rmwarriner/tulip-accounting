"""Tests for /v1/transactions."""

from __future__ import annotations

from datetime import date

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


@pytest.fixture
def cash_and_food(client: TestClient, auth_h: dict[str, str]) -> tuple[str, str]:
    cash = client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": "Cash", "type": "asset", "currency": "USD", "code": "1110"},
    ).json()
    food = client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": "Food", "type": "expense", "currency": "USD", "code": "5100"},
    ).json()
    return cash["id"], food["id"]


class TestCreateTransaction:
    def test_creates_balanced_transaction(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        today = date.today()
        body = {
            "date": today.isoformat(),
            "description": "Lunch",
            "postings": [
                {"account_id": food, "amount": "12.50", "currency": "USD"},
                {"account_id": cash, "amount": "-12.50", "currency": "USD"},
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["status"] == "posted"
        assert len(out["postings"]) == 2
        assert sum(float(p["amount"]) for p in out["postings"]) == 0.0

    def test_unbalanced_transaction_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        today = date.today()
        body = {
            "date": today.isoformat(),
            "description": "Bad",
            "postings": [
                {"account_id": food, "amount": "12.50", "currency": "USD"},
                {"account_id": cash, "amount": "-9.00", "currency": "USD"},
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        body_json = assert_problem(r, code="transaction.unbalanced", status=400)
        assert "balance" in body_json["detail"].lower()

    def test_transaction_with_unknown_account_returns_problem(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        today = date.today()
        body = {
            "date": today.isoformat(),
            "description": "Ghost account",
            "postings": [
                {
                    "account_id": "00000000-0000-0000-0000-000000000000",
                    "amount": "1.00",
                    "currency": "USD",
                },
                {
                    "account_id": "00000000-0000-0000-0000-000000000001",
                    "amount": "-1.00",
                    "currency": "USD",
                },
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        body_json = assert_problem(r, code="account.unknown", status=400)
        assert "00000000-0000-0000-0000-000000000000" in body_json["detail"]

    def test_transaction_outside_period_returns_problem(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        # Default seeded period covers the current year only.
        body = {
            "date": "1999-01-01",
            "description": "Time travel",
            "postings": [
                {"account_id": food, "amount": "1.00", "currency": "USD"},
                {"account_id": cash, "amount": "-1.00", "currency": "USD"},
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        body_json = assert_problem(r, code="period.closed", status=400)
        assert "no period" in body_json["detail"].lower()


class TestReadTransactions:
    def test_get_returns_postings(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        today = date.today()
        created = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": today.isoformat(),
                "description": "Coffee",
                "postings": [
                    {"account_id": food, "amount": "4.25", "currency": "USD"},
                    {"account_id": cash, "amount": "-4.25", "currency": "USD"},
                ],
            },
        ).json()

        r = client.get(f"/v1/transactions/{created['id']}", headers=auth_h)
        assert r.status_code == 200
        body = r.json()
        assert body["description"] == "Coffee"
        assert len(body["postings"]) == 2

    def test_list_returns_all(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        today = date.today()
        for desc in ("a", "b", "c"):
            client.post(
                "/v1/transactions",
                headers=auth_h,
                json={
                    "date": today.isoformat(),
                    "description": desc,
                    "postings": [
                        {"account_id": food, "amount": "1.00", "currency": "USD"},
                        {"account_id": cash, "amount": "-1.00", "currency": "USD"},
                    ],
                },
            )
        rows = client.get("/v1/transactions", headers=auth_h).json()
        assert len(rows) == 3
