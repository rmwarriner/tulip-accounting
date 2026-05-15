"""Tests for PATCH /v1/transactions/{id}/description — GDPR Art. 16 (issue #242).

Rectification mutates a POSTED / RECONCILED transaction's description /
reference / notes in place. Postings are untouched. The audit row captures
the old values verbatim under the Art. 17(3)(e) integrity carve-out (the
user-erasure path then nulls them when the user is deleted, per #235).

When the source has been voided, the reversal sibling's description was
constructed as ``f"Reversal of {old}: {reason}"`` — quoting the very PII
the user is now rectifying. The handler rewrites the reversal's
description in place, substituting ``[redacted]`` for the original quote
when the canonical prefix matches.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

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
    description: str = "Lunch with John Doe",
    reference: str | None = "INV-001",
    notes: str | None = "private note",
) -> dict:
    body: dict[str, object] = {
        "date": date.today().isoformat(),
        "description": description,
        "postings": [
            {"account_id": food, "amount": "12.50", "currency": "USD"},
            {"account_id": cash, "amount": "-12.50", "currency": "USD"},
        ],
    }
    if reference is not None:
        body["reference"] = reference
    if notes is not None:
        body["notes"] = notes
    r = client.post("/v1/transactions", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestRectifyHappyPath:
    def test_rectify_posted_updates_description(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Lunch with [redacted]"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["description"] == "Lunch with [redacted]"
        # Postings, status, reference, notes untouched.
        assert body["status"] == "posted"
        assert body["reference"] == "INV-001"
        assert body["notes"] == "private note"
        amounts = sorted(float(p["amount"]) for p in body["postings"])
        assert amounts == [-12.5, 12.5]

    def test_rectify_updates_reference_and_notes(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"reference": "INV-CORRECTED", "notes": "updated note"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Description untouched (omitted from body).
        assert body["description"] == "Lunch with John Doe"
        assert body["reference"] == "INV-CORRECTED"
        assert body["notes"] == "updated note"

    def test_rectify_clears_notes_with_null(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"notes": None},
        )
        assert r.status_code == 200, r.text
        assert r.json()["notes"] is None

    def test_rectify_reconciled_transaction_succeeds(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        """Rectification is allowed on RECONCILED transactions too.

        The status check accepts POSTED + RECONCILED; only PENDING is
        rejected (which has its own PATCH).
        """
        from sqlalchemy import update

        from tulip_storage.models import Transaction, TransactionStatus

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        # Force-flip the row to RECONCILED so the handler can be exercised
        # without dragging in a full reconciliation flow.
        with session_maker() as s:
            s.execute(
                update(Transaction)
                .where(Transaction.id == UUID(tx["id"]))
                .values(status=TransactionStatus.RECONCILED)
            )
            s.commit()

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Corrected"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "reconciled"


class TestRectifyAudit:
    def test_rectify_writes_description_rectified_audit_row(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Corrected description"},
        )
        assert r.status_code == 200, r.text

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "description_rectified"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        row = rows[0]
        assert row.entity_type == "transaction"
        assert str(row.entity_id) == tx["id"]
        assert row.before_snapshot is not None
        assert row.before_snapshot["description"] == "Lunch with John Doe"
        assert row.after_snapshot is not None
        assert row.after_snapshot["description"] == "Corrected description"

    def test_rectify_notes_audit_uses_presence_only(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        """Notes plaintext must not appear in audit snapshots."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food, notes="SSN: 123-45-6789")

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"notes": "SSN: REDACTED"},
        )
        assert r.status_code == 200, r.text

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "description_rectified")
            ).scalar_one()
        assert row.before_snapshot["notes_present"] is True
        assert row.after_snapshot["notes_present"] is True
        # Neither plaintext appears anywhere in the snapshots.
        before_str = str(row.before_snapshot)
        after_str = str(row.after_snapshot)
        assert "123-45-6789" not in before_str
        assert "REDACTED" not in after_str

    def test_rectify_audit_omits_keys_not_in_body(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        """If the body only changes ``description``, the audit row should
        not record reference / notes in its snapshots.
        """
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Only this changes"},
        )

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "description_rectified")
            ).scalar_one()
        assert "description" in row.before_snapshot
        assert "reference" not in row.before_snapshot
        assert "notes_present" not in row.before_snapshot
        assert "description" in row.after_snapshot
        assert "reference" not in row.after_snapshot


class TestRectifyReversalRewrite:
    def test_rectify_rewrites_reversal_description_in_place(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        """When the source has been voided, the reversal's description quotes
        the source's old description. Rectification rewrites it in place so
        the PII actually leaves the row at rest.
        """
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food, description="Pay John Doe")

        # Void → reversal's description is "Reversal of Pay John Doe: typo"
        void_r = client.post(
            f"/v1/transactions/{tx['id']}/void",
            headers=auth_h,
            json={"reason": "typo"},
        )
        assert void_r.status_code == 201
        reversal_id = void_r.json()["reversal_id"]

        rev_before = client.get(f"/v1/transactions/{reversal_id}", headers=auth_h).json()
        assert "Pay John Doe" in rev_before["description"]

        # Rectify the source — the reversal sibling should be rewritten too.
        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Pay [redacted]"},
        )
        assert r.status_code == 200, r.text

        rev_after = client.get(f"/v1/transactions/{reversal_id}", headers=auth_h).json()
        assert "John Doe" not in rev_after["description"]
        assert "[redacted]" in rev_after["description"]
        # The reason ("typo") is preserved.
        assert "typo" in rev_after["description"]

    def test_rectify_audit_metadata_carries_reversal_id_when_rewritten(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food, description="Pay John Doe")
        void_r = client.post(
            f"/v1/transactions/{tx['id']}/void",
            headers=auth_h,
            json={"reason": "typo"},
        )
        reversal_id = void_r.json()["reversal_id"]

        client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Pay [redacted]"},
        )

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "description_rectified")
            ).scalar_one()
        assert row.metadata_ is not None
        assert row.metadata_["reversal_id_rewritten"] == reversal_id

    def test_rectify_leaves_reversal_alone_when_prefix_doesnt_match(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        """If an operator has manually rewritten the reversal's description so
        it no longer starts with the canonical ``Reversal of {old}: ``
        prefix, rectifying the source must leave the reversal untouched.
        """
        from sqlalchemy import select, update

        from tulip_storage.models import Transaction

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food, description="Pay John Doe")
        void_r = client.post(
            f"/v1/transactions/{tx['id']}/void",
            headers=auth_h,
            json={"reason": "typo"},
        )
        reversal_id = void_r.json()["reversal_id"]

        # Hand-edit the reversal description out-of-band so the prefix no
        # longer matches.
        with session_maker() as s:
            s.execute(
                update(Transaction)
                .where(Transaction.id == UUID(reversal_id))
                .values(description="Unrelated note about the reversal")
            )
            s.commit()

        client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "Pay [redacted]"},
        )

        rev = client.get(f"/v1/transactions/{reversal_id}", headers=auth_h).json()
        assert rev["description"] == "Unrelated note about the reversal"

        # And the audit row's metadata reports no rewrite happened.
        from tulip_storage.models import AuditLog

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "description_rectified")
            ).scalar_one()
        if row.metadata_ is not None:
            assert "reversal_id_rewritten" not in row.metadata_


class TestRectifyErrorPaths:
    def test_rectify_pending_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker,
    ) -> None:
        """Rectification is for POSTED+ rows; PENDING uses the regular PATCH."""
        from sqlalchemy import update

        from tulip_storage.models import Transaction, TransactionStatus

        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)
        with session_maker() as s:
            s.execute(
                update(Transaction)
                .where(Transaction.id == UUID(tx["id"]))
                .values(status=TransactionStatus.PENDING)
            )
            s.commit()

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={"description": "x"},
        )
        assert_problem(r, code="transaction.not_rectifiable", status=409)

    def test_rectify_unknown_id_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        from uuid import uuid4

        r = client.patch(
            f"/v1/transactions/{uuid4()}/description",
            headers=auth_h,
            json={"description": "x"},
        )
        assert_problem(r, code="transaction.not_found", status=404)

    def test_rectify_other_household_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        """A second household's user cannot reach this household's tx."""
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        client.post(
            "/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "another good password please",
                "display_name": "Other",
                "household_name": "Other Family",
            },
        )
        login = client.post(
            "/v1/auth/login",
            json={"email": "other@example.com", "password": "another good password please"},
        )
        other_h = {"Authorization": f"Bearer {login.json()['access_token']}"}

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=other_h,
            json={"description": "x"},
        )
        assert_problem(r, code="transaction.not_found", status=404)

    def test_rectify_empty_body_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={},
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_rectify_extra_fields_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ) -> None:
        """``postings`` / ``date`` in the body — out of scope for this endpoint."""
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            headers=auth_h,
            json={
                "description": "ok",
                "postings": [{"account_id": food, "amount": "1.00", "currency": "USD"}],
            },
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_rectify_unauthenticated_returns_401(
        self,
        client: TestClient,
        cash_and_food: tuple[str, str],
        auth_h: dict[str, str],
    ) -> None:
        cash, food = cash_and_food
        tx = _post_lunch(client, auth_h, cash, food)

        r = client.patch(
            f"/v1/transactions/{tx['id']}/description",
            json={"description": "x"},
        )
        assert_problem(r, code="auth.unauthorized", status=401)
