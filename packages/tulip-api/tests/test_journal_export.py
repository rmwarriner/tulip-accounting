"""Tests for GET /v1/journal/export (P7.4).

Covers shape (header line, indented postings, blank line between txs)
+ the date-range filter + auth gate. Pending and voided transactions
are excluded.
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
    description: str = "Test",
) -> None:
    on = on or date.today()
    r = client.post(
        "/v1/transactions",
        headers=headers,
        json={
            "date": on.isoformat(),
            "description": description,
            "postings": [
                {"account_id": debit, "amount": amount, "currency": "USD"},
                {"account_id": credit, "amount": f"-{amount}", "currency": "USD"},
            ],
        },
    )
    assert r.status_code == 201, r.text


class TestJournalExport:
    def test_empty_household_returns_header_comments_only(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.get("/v1/journal/export", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/plain")
        body = r.text
        assert "Tulip Accounting" in body
        assert "household: Smith" in body
        # No transactions yet — the body should be just the comment header.
        assert "Grocery" not in body

    def test_renders_posted_transactions(self, client: TestClient, auth_h: dict[str, str]) -> None:
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(
            client,
            auth_h,
            debit=food,
            credit=cash,
            amount="12.50",
            on=date(2026, 5, 1),
            description="Grocery store",
        )

        r = client.get("/v1/journal/export", headers=auth_h)
        assert r.status_code == 200
        body = r.text
        # Header line: date + description.
        assert "2026-05-01 Grocery store" in body
        # Posting lines: two-space indent, colon-hierarchy account names.
        assert "    Expense:5100:Food  12.50 USD" in body
        assert "    Asset:1110:Cash  -12.50 USD" in body

    def test_respects_start_end_date_range(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        _post_tx(
            client,
            auth_h,
            debit=food,
            credit=cash,
            amount="5.00",
            on=date(2026, 1, 15),
            description="Early",
        )
        _post_tx(
            client,
            auth_h,
            debit=food,
            credit=cash,
            amount="7.00",
            on=date(2026, 6, 1),
            description="Late",
        )

        r = client.get(
            "/v1/journal/export?start=2026-05-01&end=2026-12-31",
            headers=auth_h,
        )
        assert r.status_code == 200
        body = r.text
        assert "Late" in body
        assert "Early" not in body
        # The comment header notes the bounds.
        assert "from: 2026-05-01" in body
        assert "to: 2026-12-31" in body

    def test_content_disposition_includes_sensible_filename(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.get("/v1/journal/export?start=2026-01-01&end=2026-12-31", headers=auth_h)
        cd = r.headers.get("content-disposition", "")
        assert "filename=" in cd
        assert ".journal" in cd
        assert "2026-01-01-2026-12-31" in cd

    def test_no_token_returns_unauthorized(self, client: TestClient) -> None:
        r = client.get("/v1/journal/export")
        assert_problem(r, code="auth.unauthorized", status=401)

    def test_account_without_code_falls_back_to_type_name_path(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Accounts without a code use ``<type>:<name>`` instead of ``<type>:<code>:<name>``."""
        cash = _account(client, auth_h, name="Cash on hand")  # no code
        food = _account(client, auth_h, name="Misc Food", type="expense")  # no code
        _post_tx(
            client,
            auth_h,
            debit=food,
            credit=cash,
            amount="3.00",
            on=date(2026, 5, 1),
            description="No-code test",
        )
        r = client.get("/v1/journal/export", headers=auth_h)
        body = r.text
        assert "Expense:Misc Food" in body
        assert "Asset:Cash on hand" in body
