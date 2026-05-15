"""Tests for POST /v1/journal/import (P7.5).

Round-trip through GET /v1/journal/export → POST /v1/journal/import
is the primary acceptance criterion: the export round-trips back
into the same household as PENDING transactions.
"""

from __future__ import annotations

from datetime import date

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


def _post_journal(client: TestClient, headers: dict[str, str], text: str) -> object:
    """Helper: POST /v1/journal/import with a plain-text body."""
    return client.post(
        "/v1/journal/import",
        headers={**headers, "content-type": "text/plain"},
        content=text,
    )


class TestImportHappyPath:
    def test_minimal_two_posting_tx_creates_pending(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        _account(client, auth_h, name="Cash", code="1110")
        _account(client, auth_h, name="Food", type="expense", code="5100")

        body = "\n".join(
            [
                "2026-05-01 Grocery store",
                "    Expense:5100:Food  12.50 USD",
                "    Asset:1110:Cash  -12.50 USD",
                "",
            ]
        )
        r = _post_journal(client, auth_h, body)
        assert r.status_code == 201, r.text
        payload = r.json()
        assert payload["created"] == 1
        assert len(payload["transaction_ids"]) == 1

        # And the transaction shows up as PENDING.
        listing = client.get("/v1/transactions", headers=auth_h, params={"status": "pending"})
        items = listing.json()
        descs = [t["description"] for t in items]
        assert "Grocery store" in descs

    def test_export_then_import_roundtrip(self, client: TestClient, auth_h: dict[str, str]) -> None:
        """Round-trip: post a tx → export → import the export → both surface as pending."""
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": "2026-05-01",
                "description": "Round-trip test",
                "postings": [
                    {"account_id": food, "amount": "7.25", "currency": "USD"},
                    {"account_id": cash, "amount": "-7.25", "currency": "USD"},
                ],
            },
        )
        assert r.status_code == 201

        # Export and re-import.
        exported = client.get("/v1/journal/export", headers=auth_h).text
        r2 = _post_journal(client, auth_h, exported)
        assert r2.status_code == 201, r2.text
        assert r2.json()["created"] == 1  # one tx survived the round-trip


class TestImportErrors:
    def test_parse_error_returns_400_with_line_numbers(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        body = "\n".join(
            [
                "not-a-date no parse possible",
                "    foo  bar",
            ]
        )
        r = _post_journal(client, auth_h, body)
        assert r.status_code == 400
        payload = r.json()
        assert payload["code"] == "journal.parse_failed"
        assert payload["errors"]
        assert "line" in payload["errors"][0]

    def test_unknown_account_returns_400_with_line_number(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        body = "\n".join(
            [
                "2026-05-01 Unknown accounts",
                "    Expense:9999:Nonexistent  5.00 USD",
                "    Asset:9998:NoSuchCash  -5.00 USD",
                "",
            ]
        )
        r = _post_journal(client, auth_h, body)
        assert r.status_code == 400
        payload = r.json()
        assert payload["code"] == "journal.import_failed"
        assert any("could not resolve" in e["message"] for e in payload["errors"])

    def test_unbalanced_postings_return_400(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        cash = _account(client, auth_h, name="Cash", code="1110")
        food = _account(client, auth_h, name="Food", type="expense", code="5100")
        del cash, food  # ensure accounts seeded; assertion comes via account paths
        body = "\n".join(
            [
                "2026-05-01 Unbalanced",
                "    Expense:5100:Food  10.00 USD",
                "    Asset:1110:Cash  -8.00 USD",
                "",
            ]
        )
        r = _post_journal(client, auth_h, body)
        assert r.status_code == 400
        payload = r.json()
        assert payload["code"] == "journal.import_failed"
        assert any("not balance" in e["message"] for e in payload["errors"])

    def test_currency_mismatch_returns_400(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        _account(client, auth_h, name="Cash", code="1110")
        _account(client, auth_h, name="Food", type="expense", code="5100")
        body = "\n".join(
            [
                "2026-05-01 Wrong currency",
                "    Expense:5100:Food  10.00 EUR",  # account is USD
                "    Asset:1110:Cash  -10.00 EUR",
                "",
            ]
        )
        r = _post_journal(client, auth_h, body)
        assert r.status_code == 400
        payload = r.json()
        assert payload["code"] == "journal.import_failed"

    def test_empty_body_creates_no_transactions(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = _post_journal(client, auth_h, "")
        # Empty body has no transactions and no errors → 201 with zero created.
        assert r.status_code == 201
        assert r.json()["created"] == 0

    def test_no_token_returns_unauthorized(self, client: TestClient) -> None:
        r = client.post(
            "/v1/journal/import",
            headers={"content-type": "text/plain"},
            content="2026-05-01 desc\n    A:B  1.00 USD\n    A:C  -1.00 USD\n",
        )
        assert r.status_code == 401


# Keep the date import in scope (used implicitly by some test bodies).
_ = date
