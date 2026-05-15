"""Tests for PATCH + DELETE /v1/transactions/{id} (P5.0, PENDING-only)."""

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


def _post_lunch(
    client: TestClient,
    auth_h: dict[str, str],
    cash: str,
    food: str,
) -> dict:
    body = {
        "date": date.today().isoformat(),
        "description": "Lunch",
        "postings": [
            {"account_id": food, "amount": "12.50", "currency": "USD"},
            {"account_id": cash, "amount": "-12.50", "currency": "USD"},
        ],
    }
    r = client.post("/v1/transactions", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _create_pending(
    client: TestClient,
    auth_h: dict[str, str],
    cash: str,
    food: str,
) -> dict:
    """Force a PENDING transaction by posting one and then... we can't via API.

    Trick: PENDING txs are created by the importer pipeline (Phase 5+). For
    now, we go directly through the storage layer to seed a PENDING row.
    """
    # The API only creates POSTED transactions today. Use the storage layer
    # directly via the dependency-injected session.
    raise NotImplementedError("seeded by helper below")


@pytest.fixture
def pending_tx(app, session_maker, auth_h: dict[str, str], cash_and_food: tuple[str, str]):
    """Insert a PENDING transaction via the storage layer.

    The HTTP API only emits POSTED transactions until importers ship in
    P5.2. We bypass via session_maker to exercise the PENDING-only edit
    and delete contracts.
    """
    from decimal import Decimal
    from uuid import UUID, uuid4

    from tulip_core.money import Money
    from tulip_core.transactions import Posting as DomainPosting
    from tulip_core.transactions import Transaction as DomainTransaction
    from tulip_core.transactions import TransactionStatus as DomainTxStatus
    from tulip_storage.repositories import TransactionRepository

    cash, food = cash_and_food
    # Decode the JWT to get household_id (test convenience).
    import base64
    import json

    token = auth_h["Authorization"].removeprefix("Bearer ")
    payload = token.split(".")[1] + "=="
    claims = json.loads(base64.urlsafe_b64decode(payload))
    household_id = UUID(claims["household_id"])

    pending_id = uuid4()
    with session_maker() as session:
        domain_tx = DomainTransaction(
            id=pending_id,
            household_id=household_id,
            date=date.today(),
            description="Pending lunch (from import)",
            postings=(
                DomainPosting(
                    id=uuid4(),
                    account_id=UUID(food),
                    amount=Money(Decimal("12.50"), "USD"),
                ),
                DomainPosting(
                    id=uuid4(),
                    account_id=UUID(cash),
                    amount=Money(Decimal("-12.50"), "USD"),
                ),
            ),
            status=DomainTxStatus.PENDING,
        )
        TransactionRepository(session, household_id).save_balanced(domain_tx)
        session.commit()
    return str(pending_id)


class TestPatch:
    def test_patches_pending_description(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        pending_tx: str,
    ):
        r = client.patch(
            f"/v1/transactions/{pending_tx}",
            headers=auth_h,
            json={"description": "Lunch (corrected)"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "Lunch (corrected)"

    def test_patches_postings_replaces_all(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        pending_tx: str,
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        r = client.patch(
            f"/v1/transactions/{pending_tx}",
            headers=auth_h,
            json={
                "postings": [
                    {"account_id": food, "amount": "20.00", "currency": "USD"},
                    {"account_id": cash, "amount": "-20.00", "currency": "USD"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        amounts = sorted(float(p["amount"]) for p in r.json()["postings"])
        assert amounts == [-20.0, 20.0]

    def test_patch_posted_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        posted = _post_lunch(client, auth_h, cash, food)
        r = client.patch(
            f"/v1/transactions/{posted['id']}",
            headers=auth_h,
            json={"description": "too late"},
        )
        assert_problem(r, code="transaction.not_editable", status=409)

    def test_patch_unknown_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        bogus = "11111111-1111-1111-1111-111111111111"
        r = client.patch(
            f"/v1/transactions/{bogus}",
            headers=auth_h,
            json={"description": "ghost"},
        )
        assert_problem(r, code="transaction.not_found", status=404)

    def test_patch_unauthenticated_returns_401(
        self,
        client: TestClient,
        pending_tx: str,
    ):
        r = client.patch(
            f"/v1/transactions/{pending_tx}",
            json={"description": "anon"},
        )
        assert r.status_code == 401


class TestDelete:
    def test_deletes_pending(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        pending_tx: str,
    ):
        r = client.delete(f"/v1/transactions/{pending_tx}", headers=auth_h)
        assert r.status_code == 204, r.text
        # Subsequent GET returns 404.
        r2 = client.get(f"/v1/transactions/{pending_tx}", headers=auth_h)
        assert_problem(r2, code="transaction.not_found", status=404)

    def test_delete_posted_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        posted = _post_lunch(client, auth_h, cash, food)
        r = client.delete(f"/v1/transactions/{posted['id']}", headers=auth_h)
        assert_problem(r, code="transaction.not_deletable", status=409)

    def test_delete_unknown_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        bogus = "11111111-1111-1111-1111-111111111111"
        r = client.delete(f"/v1/transactions/{bogus}", headers=auth_h)
        assert_problem(r, code="transaction.not_found", status=404)

    def test_delete_pending_promoted_from_import_unpromotes_statement_line(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ):
        """#301 + #302: a PENDING tx created via the imports-apply flow can
        be deleted via the API. The source statement line is un-promoted
        (re-promotable). Without the fix this returned 500 from an
        unhandled IntegrityError; with the fix it's 204.
        """
        from uuid import UUID

        from sqlalchemy import select

        from tulip_storage.models import StatementLine

        cash, _food = cash_and_food

        # Upload a minimal QIF that auto-categorizes to Imbalance:Unknown
        # via the register-time seeded chart entry (so the apply flow
        # succeeds without seeding extra category accounts).
        qif_body = b"!Type:Bank\nD1/2/26\nT-12.50\nPCoffee\n^\n"
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("coffee.qif", qif_body, "application/qif")},
            data={
                "account_id": cash,
                "source_format": "qif",
                "no_categorize": "true",
            },
        )
        assert r.status_code == 201, r.text
        batch_id = r.json()["id"]

        # Apply → one PENDING tx promoted from the single statement line.
        ap = client.post(f"/v1/imports/{batch_id}/apply", headers=auth_h)
        assert ap.status_code == 200, ap.text
        tx_ids = ap.json()["transaction_ids"]
        assert len(tx_ids) == 1
        tx_id = tx_ids[0]

        # Confirm the back-reference exists pre-delete.
        with session_maker() as s:
            line = s.execute(select(StatementLine)).scalar_one()
            assert line.promoted_transaction_id == UUID(tx_id)

        # The reproduction case from #301: DELETE on a promoted PENDING tx.
        r = client.delete(f"/v1/transactions/{tx_id}", headers=auth_h)
        assert r.status_code == 204, r.text

        # Post-delete: tx gone, line still here but un-promoted.
        with session_maker() as s:
            line = s.execute(select(StatementLine)).scalar_one()
            assert line.promoted_transaction_id is None
