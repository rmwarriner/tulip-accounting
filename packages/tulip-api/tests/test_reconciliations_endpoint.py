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

    def test_inbox_filters_lines_matched_in_prior_completed_recon(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        """#127: lines matched in a prior completed reconciliation
        must NOT show up in a subsequent reconciliation's inbox as unmatched.
        """
        # Recon A: auto-match + complete -> all lines matched.
        recon_a = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        client.post(f"/v1/reconciliations/{recon_a['id']}/auto-match", headers=auth_h)
        complete_a = client.post(f"/v1/reconciliations/{recon_a['id']}/complete", headers=auth_h)
        assert complete_a.status_code == 200, complete_a.text

        # Recon B for the same account + same batch (allowed because A is complete).
        recon_b = _create_recon(
            client,
            auth_h,
            account_id=checking_account,
            batch_id=parsed_batch,
            starting="1457.83",
            ending="1457.83",  # nothing new on the bank statement
        )
        inbox_b = client.get(f"/v1/reconciliations/{recon_b['id']}", headers=auth_h).json()
        # Lines from recon A's matches must not surface as "unmatched" in recon B.
        assert inbox_b["unmatched_statement_lines"] == []


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


# ---- POST /v1/reconciliations/{id}/matches (manual) ----------------------


class TestManualMatch:
    def test_creates_manual_match(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        # Find a line + a matching tx by date.
        from decimal import Decimal as _D

        inbox = client.get(f"/v1/reconciliations/{recon['id']}", headers=auth_h).json()
        line = next(
            line
            for line in inbox["unmatched_statement_lines"]
            if _D(line["amount"]) == _D("-42.17")
        )
        tx = next(tx for tx in inbox["unmatched_ledger_transactions"] if tx["date"] == "2026-05-12")
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/matches",
            headers=auth_h,
            json={
                "statement_line_id": line["id"],
                "ledger_transaction_id": tx["id"],
                "match_amount": "-42.17",
                "currency": "USD",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["confidence"] is None
        assert body["matcher_version"] is None
        assert body["created_by_user_id"] is not None

    def test_amount_mismatch_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        inbox = client.get(f"/v1/reconciliations/{recon['id']}", headers=auth_h).json()
        line = inbox["unmatched_statement_lines"][0]
        tx = inbox["unmatched_ledger_transactions"][0]
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/matches",
            headers=auth_h,
            json={
                "statement_line_id": line["id"],
                "ledger_transaction_id": tx["id"],
                "match_amount": "0.01",  # wrong
                "currency": "USD",
            },
        )
        assert_problem(r, status=400, code="reconciliation.line_amount_mismatch")

    def test_already_matched_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        # Auto-match populates matches; manual match on the same line must 409.
        client.post(f"/v1/reconciliations/{recon['id']}/auto-match", headers=auth_h)
        # Pick a matched line by querying the batch's lines via /imports.
        batch = client.get(f"/v1/imports/{parsed_batch}", headers=auth_h).json()
        line_id = batch["lines"][0]["id"]
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/matches",
            headers=auth_h,
            json={
                "statement_line_id": line_id,
                "ledger_transaction_id": matching_ledger_txs[0],
                "match_amount": "-42.17",
                "currency": "USD",
            },
        )
        assert_problem(r, status=409, code="reconciliation.line_already_matched")


# ---- POST/DELETE /v1/reconciliations/{id}/carry-forward[/{tx_id}] --------


class TestCarryForwardEndpoints:
    def test_add_marks_in_period_tx(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/carry-forward",
            headers=auth_h,
            json={"transaction_ids": [matching_ledger_txs[0]]},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["transaction_ids"] == [matching_ledger_txs[0]]

    def test_add_out_of_period_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
    ):
        # Create a tx outside the May period.
        # Need a second account (expense) for the balanced posting.
        ex = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Misc", "type": "expense", "currency": "USD", "code": "5500"},
        ).json()
        out_tx = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": "2026-06-15",
                "description": "Out of period",
                "postings": [
                    {"account_id": checking_account, "amount": "-5.00", "currency": "USD"},
                    {"account_id": ex["id"], "amount": "5.00", "currency": "USD"},
                ],
            },
        ).json()
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        r = client.post(
            f"/v1/reconciliations/{recon['id']}/carry-forward",
            headers=auth_h,
            json={"transaction_ids": [out_tx["id"]]},
        )
        assert_problem(r, status=400, code="reconciliation.tx_not_in_period")

    def test_remove_clears_link(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        client.post(
            f"/v1/reconciliations/{recon['id']}/carry-forward",
            headers=auth_h,
            json={"transaction_ids": [matching_ledger_txs[0]]},
        )
        r = client.delete(
            f"/v1/reconciliations/{recon['id']}/carry-forward/{matching_ledger_txs[0]}",
            headers=auth_h,
        )
        assert r.status_code == 204, r.text

    def test_complete_with_carry_forward_balances(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        parsed_batch: str,
        matching_ledger_txs: list[str],
    ):
        """Carry-forward both txs (sum 1457.83); /complete should balance with 0 matches."""
        recon = _create_recon(client, auth_h, account_id=checking_account, batch_id=parsed_batch)
        client.post(
            f"/v1/reconciliations/{recon['id']}/carry-forward",
            headers=auth_h,
            json={"transaction_ids": matching_ledger_txs},
        )
        r = client.post(f"/v1/reconciliations/{recon['id']}/complete", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "complete"
