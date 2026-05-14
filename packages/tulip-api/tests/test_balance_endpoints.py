"""Tests for the balance-read endpoints (#31, #274).

* ``GET /v1/accounts/{id}/balance`` — single-account ledger balance.
* ``GET /v1/reports/trial-balance`` — household-wide per-account
  per-currency balances + totals.

Both endpoints default to POSTED + RECONCILED only and accept an
optional ``as_of=YYYY-MM-DD`` query for point-in-time balances.
``include_pending=true`` (#274) folds PENDING transactions in.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


def _seed_pending_tx(
    session_maker: object,
    *,
    debit: str,
    credit: str,
    amount: str,
    on: date | None = None,
) -> None:
    """Insert a PENDING transaction directly via the repo.

    ``POST /v1/transactions`` always promotes to POSTED, so the only way
    to get a PENDING row for the #274 tests is through the repository —
    the same path the importer uses.
    """
    from uuid import UUID, uuid4

    from sqlalchemy import select

    from tulip_core.money import Money
    from tulip_core.transactions import Posting as DomainPosting
    from tulip_core.transactions import Transaction as DomainTransaction
    from tulip_core.transactions import TransactionStatus as DomainTxStatus
    from tulip_storage.models import Household
    from tulip_storage.repositories import TransactionRepository

    on = on or date.today()
    with session_maker() as s:  # type: ignore[operator]
        household_id = s.execute(select(Household.id)).scalar_one()
        tx = DomainTransaction(
            id=uuid4(),
            household_id=household_id,
            date=on,
            description=f"pending {amount}",
            postings=(
                DomainPosting(
                    id=uuid4(), account_id=UUID(debit), amount=Money(Decimal(amount), "USD")
                ),
                DomainPosting(
                    id=uuid4(),
                    account_id=UUID(credit),
                    amount=Money(Decimal(f"-{amount}"), "USD"),
                ),
            ),
            status=DomainTxStatus.PENDING,
        )
        TransactionRepository(s, household_id).save_balanced(tx)
        s.commit()


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

    def test_default_excludes_pending(
        self, client: TestClient, auth_h: dict[str, str], session_maker: object
    ):
        # #274: without the flag, PENDING transactions don't count.
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="10.00")
        _seed_pending_tx(session_maker, debit=food, credit=cash, amount="99.00")

        body = client.get(f"/v1/accounts/{food}/balance", headers=auth_h).json()
        assert body["balance"] == "10.00"
        assert body["pending_included"] is False
        assert body["pending_count"] == 0

    def test_include_pending_folds_in_pending(
        self, client: TestClient, auth_h: dict[str, str], session_maker: object
    ):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="10.00")
        _seed_pending_tx(session_maker, debit=food, credit=cash, amount="99.00")

        body = client.get(
            f"/v1/accounts/{food}/balance",
            headers=auth_h,
            params={"include_pending": "true"},
        ).json()
        assert body["balance"] == "109.00"
        assert body["pending_included"] is True
        assert body["pending_count"] == 1


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

    def test_default_excludes_pending(
        self, client: TestClient, auth_h: dict[str, str], session_maker: object
    ):
        # #274: default report is posted-only.
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="10.00")
        _seed_pending_tx(session_maker, debit=food, credit=cash, amount="99.00")

        body = client.get("/v1/reports/trial-balance", headers=auth_h).json()
        rows_by_account = {r["account_id"]: r for r in body["rows"]}
        assert rows_by_account[food]["balance"] == "10.00"
        assert rows_by_account[food]["has_pending"] is False
        assert body["pending_included"] is False
        assert body["pending_count"] == 0

    def test_include_pending_folds_in_and_flags_rows(
        self, client: TestClient, auth_h: dict[str, str], session_maker: object
    ):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="10.00")
        _seed_pending_tx(session_maker, debit=food, credit=cash, amount="99.00")

        body = client.get(
            "/v1/reports/trial-balance",
            headers=auth_h,
            params={"include_pending": "true"},
        ).json()
        rows_by_account = {r["account_id"]: r for r in body["rows"]}
        assert rows_by_account[food]["balance"] == "109.00"
        assert rows_by_account[food]["has_pending"] is True
        assert body["pending_included"] is True
        assert body["pending_count"] == 1

    def test_include_pending_html_shows_subtitle(
        self, client: TestClient, auth_h: dict[str, str], session_maker: object
    ):
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="10.00")
        _seed_pending_tx(session_maker, debit=food, credit=cash, amount="99.00")

        r = client.get(
            "/v1/reports/trial-balance",
            headers=auth_h,
            params={"format": "html", "include_pending": "true"},
        )
        assert r.status_code == 200, r.text
        assert "1 pending transaction" in r.text
        # The pending-affected row carries the (P) marker.
        assert "(P)" in r.text

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

    def test_format_pdf_returns_pdf_response(self, client: TestClient, auth_h: dict[str, str]):
        """``?format=pdf`` returns a real PDF via weasyprint (P7.2)."""
        _account(client, auth_h, name="Cash", code="1110")

        r = client.get("/v1/reports/trial-balance?format=pdf", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.content.startswith(b"%PDF-")
        # Filename hint includes the as_of date.
        assert "trial-balance-" in r.headers.get("content-disposition", "")

    def test_format_csv_returns_csv_response(self, client: TestClient, auth_h: dict[str, str]):
        """``?format=csv`` returns text/csv with the rows + totals (P7.3)."""
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(client, auth_h, debit=food, credit=cash, amount="12.50")

        r = client.get("/v1/reports/trial-balance?format=csv", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        body = r.content.decode()
        # Header row + at least one data row.
        assert body.startswith("Code,Account,Type,Currency,Balance\r\n")
        assert "Cash" in body
        assert "Food" in body
        assert "TOTAL" in body  # sentinel for currency totals
        assert ".csv" in r.headers.get("content-disposition", "")
