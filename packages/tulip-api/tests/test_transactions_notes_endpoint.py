"""Tests for transaction-level notes through the API (issue #271)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_token(client: TestClient) -> str:
    """Register + log in an admin; return their access token."""
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
    """Authorization header bag using the admin token."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def cash_and_food(client: TestClient, auth_h: dict[str, str]) -> tuple[str, str]:
    """Seed cash + food accounts and return their UUIDs."""
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


def _create_lunch(
    client: TestClient,
    auth_h: dict[str, str],
    cash: str,
    food: str,
    *,
    notes: str | None = None,
) -> dict:
    body: dict = {
        "date": date.today().isoformat(),
        "description": "Lunch",
        "postings": [
            {"account_id": food, "amount": "12.50", "currency": "USD"},
            {"account_id": cash, "amount": "-12.50", "currency": "USD"},
        ],
    }
    if notes is not None:
        body["notes"] = notes
    r = client.post("/v1/transactions", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestCreateWithNotes:
    def test_notes_round_trip_on_get(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        created = _create_lunch(client, auth_h, cash, food, notes="Reimbursed by Carol on 1/15.")
        tx_id = created["id"]

        # Notes round-trips on the create response itself.
        assert created["notes"] == "Reimbursed by Carol on 1/15."

        # And on subsequent GETs.
        r = client.get(f"/v1/transactions/{tx_id}", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.json()["notes"] == "Reimbursed by Carol on 1/15."

    def test_omitting_notes_yields_null_on_get(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        created = _create_lunch(client, auth_h, cash, food)
        r = client.get(f"/v1/transactions/{created['id']}", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.json()["notes"] is None


@pytest.fixture
def pending_tx_with_notes(
    app, session_maker, auth_h: dict[str, str], cash_and_food: tuple[str, str]
):
    """Seed a PENDING transaction with a notes value, via the storage layer.

    The HTTP API only emits POSTED transactions today, so we go via the
    repo to exercise the PATCH-side notes contract on PENDING rows.
    """
    import base64
    import json
    from decimal import Decimal
    from uuid import UUID, uuid4

    from tulip_api.config import get_settings
    from tulip_core.money import Money
    from tulip_core.transactions import Posting as DomainPosting
    from tulip_core.transactions import Transaction as DomainTransaction
    from tulip_core.transactions import TransactionStatus as DomainTxStatus
    from tulip_storage.repositories import TransactionRepository

    cash, food = cash_and_food
    token = auth_h["Authorization"].removeprefix("Bearer ")
    payload = token.split(".")[1] + "=="
    claims = json.loads(base64.urlsafe_b64decode(payload))
    household_id = UUID(claims["household_id"])

    tx_id = uuid4()
    settings = get_settings()
    with session_maker() as session:
        domain_tx = DomainTransaction(
            id=tx_id,
            household_id=household_id,
            date=date.today(),
            description="Pending lunch",
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
        TransactionRepository(session, household_id, master_key=settings.master_key).save_balanced(
            domain_tx, notes="original notes"
        )
        session.commit()
    return str(tx_id)


class TestPatchNotes:
    def test_patch_sets_notes(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        pending_tx_with_notes: str,
    ):
        r = client.patch(
            f"/v1/transactions/{pending_tx_with_notes}",
            headers=auth_h,
            json={"notes": "edited notes"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["notes"] == "edited notes"

    def test_patch_with_null_clears_notes(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        pending_tx_with_notes: str,
    ):
        r = client.patch(
            f"/v1/transactions/{pending_tx_with_notes}",
            headers=auth_h,
            json={"notes": None},
        )
        assert r.status_code == 200, r.text
        assert r.json()["notes"] is None

    def test_patch_omitting_notes_preserves_notes(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        pending_tx_with_notes: str,
    ):
        r = client.patch(
            f"/v1/transactions/{pending_tx_with_notes}",
            headers=auth_h,
            json={"description": "Lunch (renamed)"},  # no `notes` key
        )
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "Lunch (renamed)"
        # Notes preserved.
        assert r.json()["notes"] == "original notes"


class TestEncryptedAtRest:
    def test_notes_stored_as_ciphertext_not_plaintext(
        self,
        app,
        session_maker,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        """Encrypted-at-rest invariant via the API: raw row bytes != plaintext."""
        from uuid import UUID

        from tulip_storage.models import Transaction as TxModel

        cash, food = cash_and_food
        plaintext = "Sensitive memo content"
        created = _create_lunch(client, auth_h, cash, food, notes=plaintext)
        tx_id = UUID(created["id"])

        with session_maker() as session:
            row = session.query(TxModel).filter_by(id=tx_id).one()
            assert row.notes_encrypted is not None
            assert row.notes_encrypted != plaintext.encode("utf-8")
            assert plaintext.encode("utf-8") not in row.notes_encrypted
