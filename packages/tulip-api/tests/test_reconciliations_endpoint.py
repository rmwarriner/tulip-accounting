"""Tests for /v1/reconciliations endpoints (P5.4.b)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem

_OFX_FIXTURES = (
    Path(__file__).resolve().parents[2] / "tulip-importers" / "tests" / "fixtures" / "ofx"
)


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
def checking_account(client: TestClient, auth_h: dict[str, str]) -> str:
    r = client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": "Checking", "type": "asset", "currency": "USD", "code": "1110"},
    )
    return r.json()["id"]


@pytest.fixture
def expense_account(client: TestClient, auth_h: dict[str, str]) -> str:
    r = client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": "Misc Expense", "type": "expense", "currency": "USD", "code": "5100"},
    )
    return r.json()["id"]


@pytest.fixture
def parsed_batch(client: TestClient, auth_h: dict[str, str], checking_account: str) -> str:
    """Upload an OFX batch (lines: -42.17 on 2026-05-12, +1500.00 on 2026-05-15; net 1457.83)."""
    body = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
    r = client.post(
        "/v1/imports",
        headers=auth_h,
        files={"file": ("x.ofx", body, "application/x-ofx")},
        data={"account_id": checking_account, "source_format": "ofx"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _post_tx(
    client: TestClient,
    auth_h: dict[str, str],
    *,
    checking_id: str,
    other_id: str,
    amount: str,
    description: str,
    posted_date: str,
) -> dict:
    """POST a balanced transaction; returns the response body."""
    from decimal import Decimal

    neg = str(-Decimal(amount))
    r = client.post(
        "/v1/transactions",
        headers=auth_h,
        json={
            "date": posted_date,
            "description": description,
            "postings": [
                {"account_id": checking_id, "amount": amount, "currency": "USD"},
                {"account_id": other_id, "amount": neg, "currency": "USD"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def matching_ledger_txs(
    client: TestClient,
    auth_h: dict[str, str],
    checking_account: str,
    expense_account: str,
) -> list[str]:
    """Two POSTED ledger txs that match the OFX lines (-42.17 5/12, +1500.00 5/15)."""
    a = _post_tx(
        client,
        auth_h,
        checking_id=checking_account,
        other_id=expense_account,
        amount="-42.17",
        description="PAYPAL",
        posted_date="2026-05-12",
    )
    b = _post_tx(
        client,
        auth_h,
        checking_id=checking_account,
        other_id=expense_account,
        amount="1500.00",
        description="PAYROLL",
        posted_date="2026-05-15",
    )
    return [a["id"], b["id"]]


def _create_recon(
    client: TestClient,
    auth_h: dict[str, str],
    *,
    account_id: str,
    batch_id: str,
    starting: str = "0.00",
    ending: str = "1457.83",
) -> dict[str, str]:
    """Open a reconciliation envelope."""
    r = client.post(
        "/v1/reconciliations",
        headers=auth_h,
        json={
            "account_id": account_id,
            "statement_period_start": "2026-05-01",
            "statement_period_end": "2026-05-31",
            "statement_starting_balance": starting,
            "statement_ending_balance": ending,
            "currency": "USD",
            "source_import_batch_id": batch_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---- POST /v1/reconciliations --------------------------------------------


class TestCreateReconciliation:
    def test_creates_envelope_in_progress(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        body = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        assert body["status"] == "in_progress"
        assert body["account_id"] == checking_account
        assert body["source_import_batch_id"] == parsed_batch

    def test_unknown_account_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        parsed_batch: str,
    ):
        r = client.post(
            "/v1/reconciliations",
            headers=auth_h,
            json={
                "account_id": str(uuid4()),
                "statement_period_start": "2026-05-01",
                "statement_period_end": "2026-05-31",
                "statement_starting_balance": "0.00",
                "statement_ending_balance": "0.00",
                "currency": "USD",
                "source_import_batch_id": parsed_batch,
            },
        )
        assert_problem(r, status=404, code="account.not_found")

    def test_unknown_batch_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        r = client.post(
            "/v1/reconciliations",
            headers=auth_h,
            json={
                "account_id": checking_account,
                "statement_period_start": "2026-05-01",
                "statement_period_end": "2026-05-31",
                "statement_starting_balance": "0.00",
                "statement_ending_balance": "0.00",
                "currency": "USD",
                "source_import_batch_id": str(uuid4()),
            },
        )
        assert_problem(r, status=404, code="import_batch.not_found")

    def test_currency_mismatch_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        r = client.post(
            "/v1/reconciliations",
            headers=auth_h,
            json={
                "account_id": checking_account,
                "statement_period_start": "2026-05-01",
                "statement_period_end": "2026-05-31",
                "statement_starting_balance": "0.00",
                "statement_ending_balance": "0.00",
                "currency": "EUR",
                "source_import_batch_id": parsed_batch,
            },
        )
        assert_problem(r, status=400, code="reconciliation.currency_mismatch")

    def test_second_in_progress_for_same_account_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        r = client.post(
            "/v1/reconciliations",
            headers=auth_h,
            json={
                "account_id": checking_account,
                "statement_period_start": "2026-06-01",
                "statement_period_end": "2026-06-30",
                "statement_starting_balance": "0.00",
                "statement_ending_balance": "0.00",
                "currency": "USD",
                "source_import_batch_id": parsed_batch,
            },
        )
        assert_problem(r, status=409, code="reconciliation.account_already_in_progress")


# ---- GET /v1/reconciliations/{id} ----------------------------------------


class TestGetReconciliationInbox:
    def test_returns_envelope_plus_inbox(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        r = client.get(f"/v1/reconciliations/{recon['id']}", headers=auth_h)
        assert r.status_code == 200
        body = r.json()
        assert body["reconciliation"]["id"] == recon["id"]
        assert body["matches"] == []
        # Both lines from the batch are unmatched.
        assert len(body["unmatched_statement_lines"]) == 2
        assert body["unmatched_ledger_transactions"] == []  # no ledger txs yet

    def test_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get(f"/v1/reconciliations/{uuid4()}", headers=auth_h)
        assert_problem(r, status=404, code="reconciliation.not_found")


# ---- POST /v1/reconciliations/{id}/auto-match ----------------------------


class TestAutoMatchEndpoint:
    def test_runs_matcher_and_persists(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/auto-match",
            headers=auth_h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # No ledger txs in the period yet, so matcher emits 0 candidates.
        assert body["matches_created"] == 0
        assert body["candidate_summary"] == {"high": 0, "medium": 0, "low": 0}

    def test_re_run_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        first = client.post(
            f"/v1/reconciliations/{recon['id']}/auto-match",
            headers=auth_h,
        )
        assert first.json()["matches_created"] >= 1, first.text
        second = client.post(
            f"/v1/reconciliations/{recon['id']}/auto-match",
            headers=auth_h,
        )
        assert_problem(second, status=409, code="reconciliation.matches_exist")


# ---- POST /v1/reconciliations/{id}/matches/{match_id}/reject -------------


class TestRejectMatch:
    def test_rejects_and_returns_to_unmatched(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        client.post(
            f"/v1/reconciliations/{recon['id']}/auto-match",
            headers=auth_h,
        )
        inbox = client.get(f"/v1/reconciliations/{recon['id']}", headers=auth_h).json()
        assert len(inbox["matches"]) >= 1
        match_id = inbox["matches"][0]["id"]
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/matches/{match_id}/reject",
            headers=auth_h,
        )
        assert r.status_code == 204
        # Match gone, line back in unmatched pool.
        post_inbox = client.get(f"/v1/reconciliations/{recon['id']}", headers=auth_h).json()
        assert all(m["id"] != match_id for m in post_inbox["matches"])

    def test_unknown_match_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/matches/{uuid4()}/reject",
            headers=auth_h,
        )
        assert_problem(r, status=404, code="reconciliation_match.not_found")


# ---- POST /v1/reconciliations/{id}/complete ------------------------------


class TestCompleteEndpoint:
    def test_balanced_completes(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        # minimal_ofx2.ofx net: -42.17 + 1500.00 = 1457.83
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
            starting="0.00",
            ending="1457.83",
        )
        client.post(
            f"/v1/reconciliations/{recon['id']}/auto-match",
            headers=auth_h,
        )
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/complete",
            headers=auth_h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        assert body["affected_transaction_count"] >= 1

    def test_unbalanced_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
            starting="0.00",
            ending="1457.83",
        )
        # No matches yet -> matched_net == 0; expected_net == 2426.58.
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/complete",
            headers=auth_h,
        )
        assert_problem(r, status=409, code="reconciliation.unbalanced")
        body = r.json()
        from decimal import Decimal

        assert Decimal(body["expected_net"]) == Decimal("1457.83")
        assert Decimal(body["matched_net"]) == Decimal("0")


# ---- DELETE /v1/reconciliations/{id} -------------------------------------


class TestDeleteReconciliation:
    def test_no_cascade_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
        )
        r = client.delete(
            f"/v1/reconciliations/{recon['id']}",
            headers=auth_h,
        )
        assert_problem(r, status=400, code="reconciliation.cascade_required")

    def test_with_cascade_reverts_completed(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
            starting="0.00",
            ending="1457.83",
        )
        client.post(
            f"/v1/reconciliations/{recon['id']}/auto-match",
            headers=auth_h,
        )
        client.post(
            f"/v1/reconciliations/{recon['id']}/complete",
            headers=auth_h,
        )
        r = client.delete(
            f"/v1/reconciliations/{recon['id']}?cascade=true",
            headers=auth_h,
        )
        assert r.status_code == 204, r.text
        # Reconciliation gone.
        gone = client.get(f"/v1/reconciliations/{recon['id']}", headers=auth_h)
        assert_problem(gone, status=404, code="reconciliation.not_found")
