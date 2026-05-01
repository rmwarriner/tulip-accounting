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


class TestListTransactionsFilters:
    """`GET /v1/transactions` filter query params (P3.6)."""

    @pytest.fixture
    def seeded(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> tuple[str, str, str]:
        """Seed three transactions across different dates and a third account.

        Returns ``(cash_id, food_id, rent_id)``.
        """
        cash, food = cash_and_food
        rent = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Rent", "type": "expense", "currency": "USD", "code": "5200"},
        ).json()["id"]

        for tx_date, desc, debit_account, amount in [
            (date(date.today().year, 1, 15), "lunch-jan", food, "10.00"),
            (date(date.today().year, 6, 15), "rent-jun", rent, "1500.00"),
            (date(date.today().year, 11, 15), "lunch-nov", food, "12.00"),
        ]:
            r = client.post(
                "/v1/transactions",
                headers=auth_h,
                json={
                    "date": tx_date.isoformat(),
                    "description": desc,
                    "postings": [
                        {"account_id": debit_account, "amount": amount, "currency": "USD"},
                        {"account_id": cash, "amount": f"-{amount}", "currency": "USD"},
                    ],
                },
            )
            assert r.status_code == 201, r.text

        return cash, food, rent

    def test_filter_by_account(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        seeded: tuple[str, str, str],
    ):
        _cash, food, rent = seeded
        food_rows = client.get(
            "/v1/transactions", headers=auth_h, params={"account_id": food}
        ).json()
        assert {r["description"] for r in food_rows} == {"lunch-jan", "lunch-nov"}
        rent_rows = client.get(
            "/v1/transactions", headers=auth_h, params={"account_id": rent}
        ).json()
        assert {r["description"] for r in rent_rows} == {"rent-jun"}

    def test_filter_by_date_range(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        seeded: tuple[str, str, str],
    ):
        year = date.today().year
        rows = client.get(
            "/v1/transactions",
            headers=auth_h,
            params={"from": f"{year}-06-01", "to": f"{year}-06-30"},
        ).json()
        assert {r["description"] for r in rows} == {"rent-jun"}

    def test_filter_by_status(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        seeded: tuple[str, str, str],
    ):
        # All seeded transactions land as POSTED, so 'posted' returns three
        # and 'pending' / 'reconciled' return none.
        posted = client.get("/v1/transactions", headers=auth_h, params={"status": "posted"}).json()
        assert len(posted) == 3
        pending = client.get(
            "/v1/transactions", headers=auth_h, params={"status": "pending"}
        ).json()
        assert pending == []

    def test_invalid_status_rejected_with_validation_failed(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        r = client.get("/v1/transactions", headers=auth_h, params={"status": "bogus"})
        assert_problem(r, code="validation.failed", status=422)

    def test_limit_caps_results(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        seeded: tuple[str, str, str],
    ):
        rows = client.get("/v1/transactions", headers=auth_h, params={"limit": 2}).json()
        assert len(rows) == 2
        # Newest first, so the November lunch and June rent.
        assert {r["description"] for r in rows} == {"lunch-nov", "rent-jun"}

    def test_limit_out_of_range_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        r = client.get("/v1/transactions", headers=auth_h, params={"limit": 0})
        assert_problem(r, code="validation.failed", status=422)
        r = client.get("/v1/transactions", headers=auth_h, params={"limit": 99999})
        assert_problem(r, code="validation.failed", status=422)

    def test_filters_compose(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        seeded: tuple[str, str, str],
    ):
        _cash, food, _rent = seeded
        year = date.today().year
        rows = client.get(
            "/v1/transactions",
            headers=auth_h,
            params={
                "account_id": food,
                "from": f"{year}-10-01",
                "status": "posted",
            },
        ).json()
        assert {r["description"] for r in rows} == {"lunch-nov"}
