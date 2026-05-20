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
        assert r.status_code == 200
        # The response is honest: DELETE deactivates, it doesn't erase (#236).
        assert r.json() == {
            "action": "deactivated",
            "data_retained": [
                "name",
                "external_account_number_encrypted",
                "notes_encrypted",
            ],
        }

        # No longer listed.
        rows = client.get("/v1/accounts", headers=auth_h).json()
        assert all(row["id"] != a["id"] for row in rows)

    def test_redact_account_nulls_pii_and_keeps_postings(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ):
        """#236: redact erases PII but ledger postings still link to the account."""
        from datetime import date
        from decimal import Decimal
        from uuid import UUID

        from sqlalchemy import select

        from tulip_storage.models import Account, Posting

        sensitive = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Sensitive Acct", "type": "asset", "currency": "USD"},
        ).json()
        counterpart = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Groceries", "type": "expense", "currency": "USD"},
        ).json()
        # Post a balanced transaction touching the sensitive account.
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Lunch",
                "postings": [
                    {"account_id": counterpart["id"], "amount": "12.50", "currency": "USD"},
                    {"account_id": sensitive["id"], "amount": "-12.50", "currency": "USD"},
                ],
            },
        ).raise_for_status()

        client.delete(f"/v1/accounts/{sensitive['id']}", headers=auth_h).raise_for_status()
        r = client.post(f"/v1/accounts/{sensitive['id']}/redact", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "action": "redacted",
            "fields_redacted": [
                "name",
                "external_account_number_encrypted",
                "notes_encrypted",
            ],
        }
        with session_maker() as s:
            row = s.execute(select(Account).where(Account.id == UUID(sensitive["id"]))).scalar_one()
            postings = list(
                s.execute(select(Posting).where(Posting.account_id == UUID(sensitive["id"])))
                .scalars()
                .all()
            )
        # PII erased.
        assert row.name != "Sensitive Acct"
        assert row.name.startswith("redacted-account-")
        assert row.external_account_number_encrypted is None
        assert row.notes_encrypted is None
        # Ledger history preserved — the posting still links to the redacted account.
        assert len(postings) == 1
        assert postings[0].amount == Decimal("-12.50")

    def test_redact_active_account_returns_409(self, client: TestClient, auth_h: dict[str, str]):
        """An account must be deactivated before it can be redacted (#236)."""
        a = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Still Active", "type": "asset", "currency": "USD"},
        ).json()
        r = client.post(f"/v1/accounts/{a['id']}/redact", headers=auth_h)
        assert_problem(r, code="account.not_redactable", status=409)

    def test_redact_unknown_account_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        from uuid import uuid4

        r = client.post(f"/v1/accounts/{uuid4()}/redact", headers=auth_h)
        assert_problem(r, code="account.not_found", status=404)

    def test_redact_requires_auth(self, client: TestClient):
        from uuid import uuid4

        r = client.post(f"/v1/accounts/{uuid4()}/redact")
        assert r.status_code == 401

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


# ---- #52: placeholder flag (leaf-only postings) -----------------------------


class TestAccountPlaceholder:
    """The placeholder flag rejects postings against placeholder accounts."""

    def test_create_with_placeholder_default_false(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "X", "type": "asset", "currency": "USD"},
        )
        assert r.status_code == 201
        assert r.json()["is_placeholder"] is False

    def test_create_with_placeholder_true(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Current Assets",
                "type": "asset",
                "currency": "USD",
                "is_placeholder": True,
            },
        )
        assert r.status_code == 201
        assert r.json()["is_placeholder"] is True

    def test_patch_flips_placeholder(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "X", "type": "asset", "currency": "USD"},
        ).json()
        r = client.patch(
            f"/v1/accounts/{created['id']}",
            headers=auth_h,
            json={"is_placeholder": True},
        )
        assert r.status_code == 200
        assert r.json()["is_placeholder"] is True

    def test_posting_to_placeholder_rejected(self, client: TestClient, auth_h: dict[str, str]):
        # Create a placeholder asset + a real expense.
        ph = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Current Assets",
                "type": "asset",
                "currency": "USD",
                "is_placeholder": True,
            },
        ).json()
        food = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Food", "type": "expense", "currency": "USD"},
        ).json()

        # Posting against the placeholder rejects with the typed problem.
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": "2026-05-20",
                "description": "lunch",
                "postings": [
                    {
                        "account_id": ph["id"],
                        "amount": "-10.00",
                        "currency": "USD",
                    },
                    {
                        "account_id": food["id"],
                        "amount": "10.00",
                        "currency": "USD",
                    },
                ],
            },
        )
        assert_problem(r, status=400, code="account.placeholder_posting")
        assert r.json()["account_id"] == ph["id"]

    def test_patch_to_placeholder_rejected_when_postings_exist(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        # Create two real accounts, post a transaction, then try to
        # flip one of them to placeholder.
        cash = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Cash", "type": "asset", "currency": "USD"},
        ).json()
        food = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Food", "type": "expense", "currency": "USD"},
        ).json()
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": "2026-05-20",
                "description": "lunch",
                "postings": [
                    {"account_id": cash["id"], "amount": "-10.00", "currency": "USD"},
                    {"account_id": food["id"], "amount": "10.00", "currency": "USD"},
                ],
            },
        )

        r = client.patch(
            f"/v1/accounts/{cash['id']}",
            headers=auth_h,
            json={"is_placeholder": True},
        )
        assert_problem(r, status=409, code="account.placeholder_has_postings")
        assert r.json()["account_id"] == cash["id"]
        assert r.json()["posting_count"] >= 1

    def test_unsetting_placeholder_is_unblocked(self, client: TestClient, auth_h: dict[str, str]):
        # Flipping placeholder → false is unconditional (no postings yet).
        ph = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "X",
                "type": "asset",
                "currency": "USD",
                "is_placeholder": True,
            },
        ).json()
        r = client.patch(
            f"/v1/accounts/{ph['id']}",
            headers=auth_h,
            json={"is_placeholder": False},
        )
        assert r.status_code == 200
        assert r.json()["is_placeholder"] is False
