"""Tests for the balance-read endpoints (#31).

* ``GET /v1/accounts/{id}/balance`` — single-account ledger balance.
* ``GET /v1/reports/trial-balance`` — household-wide per-account
  per-currency balances + totals.

Both endpoints exclude pending transactions (POSTED + RECONCILED only)
and accept an optional ``as_of=YYYY-MM-DD`` query for point-in-time
balances.
"""

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
    return str(r.json()["access_token"])


@pytest.fixture
def auth_h(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def _account(client: TestClient, headers: dict[str, str], **extra: object) -> str:
    body = {"name": "X", "type": "asset", "currency": "USD"}
    body.update(extra)
    return str(client.post("/v1/accounts", headers=headers, json=body).json()["id"])


def _post_tx(
    client: TestClient,
    headers: dict[str, str],
    *,
    debit: str,
    credit: str,
    amount: str,
    on: date | None = None,
) -> None:
    on = on or date.today()
    r = client.post(
        "/v1/transactions",
        headers=headers,
        json={
            "date": on.isoformat(),
            "description": f"{amount}",
            "postings": [
                {"account_id": debit, "amount": amount, "currency": "USD"},
                {"account_id": credit, "amount": f"-{amount}", "currency": "USD"},
            ],
        },
    )
    assert r.status_code == 201, r.text


class TestAccountBalance:
    def test_returns_zero_for_account_with_no_postings(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        cash = _account(client, auth_h, name="Cash", code="1110")
        r = client.get(f"/v1/accounts/{cash}/balance", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["account_id"] == cash
        assert body["currency"] == "USD"
        assert body["balance"] == "0.00"

    def test_returns_summed_balance(self, client: TestClient, auth_h: dict[str, str]):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="12.50")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="7.25")

        r = client.get(f"/v1/accounts/{food}/balance", headers=auth_h)
        body = r.json()
        assert body["balance"] == "19.75"

        r = client.get(f"/v1/accounts/{cash}/balance", headers=auth_h)
        body = r.json()
        assert body["balance"] == "-19.75"

    def test_respects_as_of(self, client: TestClient, auth_h: dict[str, str]):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="20.00", on=date(2026, 1, 15))
        _post_tx(client, auth_h, debit=food, credit=cash, amount="30.00", on=date(2026, 6, 1))

        r = client.get(
            f"/v1/accounts/{food}/balance",
            headers=auth_h,
            params={"as_of": "2026-05-01"},
        )
        assert r.json()["balance"] == "20.00"

    def test_unknown_account_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        from uuid import uuid4

        r = client.get(f"/v1/accounts/{uuid4()}/balance", headers=auth_h)
        assert_problem(r, code="account.not_found", status=404)

    def test_no_token_returns_unauthorized(self, client: TestClient, auth_h: dict[str, str]):
        cash = _account(client, auth_h, name="Cash", code="1110")
        r = client.get(f"/v1/accounts/{cash}/balance")
        assert_problem(r, code="auth.unauthorized", status=401)


class TestTrialBalance:
    def test_empty_household_yields_empty_rows(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/reports/trial-balance", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rows"] == []
        assert body["totals_by_currency"] == []
        assert body["as_of"]  # always present, defaults to today

    def test_returns_per_account_per_currency_rows(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="12.50")

        body = client.get("/v1/reports/trial-balance", headers=auth_h).json()
        rows_by_account = {r["account_id"]: r for r in body["rows"]}
        assert rows_by_account[food]["balance"] == "12.50"
        assert rows_by_account[food]["code"] == "5100"
        assert rows_by_account[food]["name"] == "Food"
        assert rows_by_account[food]["type"] == "expense"
        assert rows_by_account[food]["currency"] == "USD"
        assert rows_by_account[cash]["balance"] == "-12.50"

    def test_totals_by_currency_sum_to_zero(self, client: TestClient, auth_h: dict[str, str]):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="12.50")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="7.50")

        body = client.get("/v1/reports/trial-balance", headers=auth_h).json()
        totals = {t["currency"]: t for t in body["totals_by_currency"]}
        assert totals["USD"]["debits"] == "20.00"
        assert totals["USD"]["credits"] == "20.00"

    def test_respects_as_of(self, client: TestClient, auth_h: dict[str, str]):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="5.00", on=date(2026, 1, 15))
        _post_tx(client, auth_h, debit=food, credit=cash, amount="7.00", on=date(2026, 8, 1))

        body = client.get(
            "/v1/reports/trial-balance",
            headers=auth_h,
            params={"as_of": "2026-05-01"},
        ).json()
        rows_by_account = {r["account_id"]: r for r in body["rows"]}
        assert rows_by_account[food]["balance"] == "5.00"
        assert body["as_of"] == "2026-05-01"

    def test_no_token_returns_unauthorized(self, client: TestClient):
        r = client.get("/v1/reports/trial-balance")
        assert_problem(r, code="auth.unauthorized", status=401)

    def test_format_html_returns_html_response(self, client: TestClient, auth_h: dict[str, str]):
        """``?format=html`` returns toner-friendly HTML rendered by tulip-reports (P7.1)."""
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="12.50")

        r = client.get("/v1/reports/trial-balance?format=html", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        # Toner-friendly contract surfaces in the rendered HTML.
        assert "Trial balance" in body
        assert "background: #fff" in body
        # Both accounts surface in the table.
        assert "Cash" in body
        assert "Food" in body
        # Money filter applied (thousand separators + 2 decimals).
        assert "12.50" in body
