"""Tests for POST /v1/transactions/{id}/void (P5.0)."""

from __future__ import annotations

from datetime import date, timedelta

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


def _post_lunch(
    client: TestClient,
    auth_h: dict[str, str],
    cash: str,
    food: str,
    *,
    when: date | None = None,
) -> dict:
    body = {
        "date": (when or date.today()).isoformat(),
        "description": "Lunch",
        "postings": [
            {"account_id": food, "amount": "12.50", "currency": "USD"},
            {"account_id": cash, "amount": "-12.50", "currency": "USD"},
        ],
    }
    r = client.post("/v1/transactions", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestVoidHappyPath:
    def test_void_creates_reversal_and_links_source(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        source = _post_lunch(client, auth_h, cash, food)

        r = client.post(
            f"/v1/transactions/{source['id']}/void",
            headers=auth_h,
            json={"reason": "duplicate charge"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["source_id"] == source["id"]
        assert body["reversal_id"] != source["id"]
        assert body["voided_at"] is not None

        # Source now reports voided_by_transaction_id.
        s = client.get(f"/v1/transactions/{source['id']}", headers=auth_h).json()
        assert s["voided_by_transaction_id"] == body["reversal_id"]
        assert s["voided_at"] is not None

        # Reversal is a real POSTED transaction with sign-flipped amounts.
        rev = client.get(f"/v1/transactions/{body['reversal_id']}", headers=auth_h).json()
        assert rev["status"] == "posted"
        assert rev["voided_by_transaction_id"] is None
        amounts = sorted(float(p["amount"]) for p in rev["postings"])
        assert amounts == [-12.5, 12.5]
        # Reversal description carries the reason.
        assert "duplicate charge" in rev["description"].lower()

    def test_void_default_date_is_today(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        source = _post_lunch(client, auth_h, cash, food)

        r = client.post(
            f"/v1/transactions/{source['id']}/void",
            headers=auth_h,
            json={"reason": "test"},
        )
        assert r.status_code == 201, r.text
        rev = client.get(f"/v1/transactions/{r.json()['reversal_id']}", headers=auth_h).json()
        assert rev["date"] == date.today().isoformat()

    def test_void_with_explicit_reversal_date(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        # Source dated 5 days ago.
        source = _post_lunch(client, auth_h, cash, food, when=date.today() - timedelta(days=5))

        # Reversal explicitly dated 2 days ago.
        explicit = (date.today() - timedelta(days=2)).isoformat()
        r = client.post(
            f"/v1/transactions/{source['id']}/void",
            headers=auth_h,
            json={"reason": "x", "reversal_date": explicit},
        )
        assert r.status_code == 201, r.text
        rev = client.get(f"/v1/transactions/{r.json()['reversal_id']}", headers=auth_h).json()
        assert rev["date"] == explicit


class TestVoidErrorPaths:
    def test_unknown_source_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        bogus = "11111111-1111-1111-1111-111111111111"
        r = client.post(
            f"/v1/transactions/{bogus}/void",
            headers=auth_h,
            json={"reason": "x"},
        )
        assert_problem(r, code="transaction.not_found", status=404)

    def test_already_voided_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        source = _post_lunch(client, auth_h, cash, food)
        first = client.post(
            f"/v1/transactions/{source['id']}/void",
            headers=auth_h,
            json={"reason": "x"},
        )
        assert first.status_code == 201, first.text
        second = client.post(
            f"/v1/transactions/{source['id']}/void",
            headers=auth_h,
            json={"reason": "y"},
        )
        body = assert_problem(second, code="transaction.already_voided", status=409)
        assert body["voided_by_transaction_id"] == first.json()["reversal_id"]

    def test_unauthenticated_returns_401(
        self,
        client: TestClient,
        cash_and_food: tuple[str, str],
        auth_h: dict[str, str],
    ):
        cash, food = cash_and_food
        source = _post_lunch(client, auth_h, cash, food)
        r = client.post(
            f"/v1/transactions/{source['id']}/void",
            json={"reason": "x"},
        )
        assert r.status_code == 401


class TestVoidWithShadowPair:
    """Voiding a pool-tagged main tx must auto-void the paired shadow tx (option c)."""

    @pytest.fixture
    def envelope(self, client: TestClient, auth_h: dict[str, str]) -> str:
        # Seed Unallocated, create an envelope, refill it to 25.00 so a
        # subsequent $25 spend lands the balance at zero.
        seed = client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "100.00",
                "currency": "USD",
                "date": date.today().isoformat(),
                "description": "Seed",
            },
        )
        assert seed.status_code in (200, 201), seed.text
        r = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "accumulate",
                "refill_rule": {
                    "strategy": "fixed_amount",
                    "amount": "25.00",
                    "currency": "USD",
                },
            },
        )
        assert r.status_code == 201, r.text
        env_id = r.json()["id"]
        ref = client.post(
            f"/v1/envelopes/{env_id}/refill",
            headers=auth_h,
            json={
                "amount": "25.00",
                "date": date.today().isoformat(),
                "description": "Initial refill",
            },
        )
        assert ref.status_code in (200, 201), ref.text
        return env_id

    def test_voiding_pool_tagged_tx_auto_voids_paired_shadow_tx(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        envelope: str,
    ):
        cash, food = cash_and_food
        # Spend $25 from the envelope.
        spend = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Groceries run",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "25.00",
                        "currency": "USD",
                        "pool_id": envelope,
                    },
                    {"account_id": cash, "amount": "-25.00", "currency": "USD"},
                ],
            },
        )
        assert spend.status_code == 201, spend.text
        spend_body = spend.json()
        assert spend_body["paired_shadow_tx_id"] is not None

        # Envelope balance reflects the spend (25 refilled, 25 spent → 0).
        bal_before = client.get(f"/v1/envelopes/{envelope}/balance", headers=auth_h).json()
        assert float(bal_before["balance"]) == 0.0

        # Void the spend.
        r = client.post(
            f"/v1/transactions/{spend_body['id']}/void",
            headers=auth_h,
            json={"reason": "wrong card"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Response surfaces the shadow void.
        assert body["paired_shadow_tx_id_voided"] == spend_body["paired_shadow_tx_id"]

        # Envelope balance reverts to the refilled amount (the voided shadow
        # tx is excluded from balance_for_pool).
        bal_after = client.get(f"/v1/envelopes/{envelope}/balance", headers=auth_h).json()
        assert float(bal_after["balance"]) == 25.0
