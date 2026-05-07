"""Tests for GET /v1/reconciliations (list endpoint, P5.4.d)."""

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


def _create_account(client: TestClient, auth_h: dict[str, str], code: str, name: str) -> str:
    return client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": name, "type": "asset", "currency": "USD", "code": code},
    ).json()["id"]


def _create_batch(client: TestClient, auth_h: dict[str, str], account_id: str) -> str:
    body = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
    return client.post(
        "/v1/imports",
        headers=auth_h,
        files={"file": ("x.ofx", body, "application/x-ofx")},
        data={"account_id": account_id, "source_format": "ofx"},
    ).json()["id"]


def _create_recon(
    client: TestClient,
    auth_h: dict[str, str],
    *,
    account_id: str,
    batch_id: str,
    period_start: str = "2026-05-01",
    period_end: str = "2026-05-31",
    starting: str = "0.00",
    ending: str = "1457.83",
) -> str:
    return client.post(
        "/v1/reconciliations",
        headers=auth_h,
        json={
            "account_id": account_id,
            "statement_period_start": period_start,
            "statement_period_end": period_end,
            "statement_starting_balance": starting,
            "statement_ending_balance": ending,
            "currency": "USD",
            "source_import_batch_id": batch_id,
        },
    ).json()["id"]


# ---- happy paths ---------------------------------------------------------


class TestListReconciliations:
    def test_empty_household_returns_empty_items(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/reconciliations", headers=auth_h)
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": []}

    def test_returns_all_reconciliations_for_household(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        a1 = _create_account(client, auth_h, "1110", "Checking")
        b1 = _create_batch(client, auth_h, a1)
        r1 = _create_recon(client, auth_h, account_id=a1, batch_id=b1)
        r = client.get("/v1/reconciliations", headers=auth_h)
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["items"]]
        assert r1 in ids

    def test_filtered_by_account_id(self, client: TestClient, auth_h: dict[str, str]):
        a1 = _create_account(client, auth_h, "1110", "Checking")
        a2 = _create_account(client, auth_h, "1120", "Savings")
        b1 = _create_batch(client, auth_h, a1)
        b2 = _create_batch(client, auth_h, a2)
        r1 = _create_recon(client, auth_h, account_id=a1, batch_id=b1)
        r2 = _create_recon(client, auth_h, account_id=a2, batch_id=b2)
        r = client.get("/v1/reconciliations", headers=auth_h, params={"account_id": a1})
        items = r.json()["items"]
        ids = {item["id"] for item in items}
        assert r1 in ids
        assert r2 not in ids

    def test_filtered_by_status(self, client: TestClient, auth_h: dict[str, str]):
        a1 = _create_account(client, auth_h, "1110", "Checking")
        b1 = _create_batch(client, auth_h, a1)
        r1 = _create_recon(client, auth_h, account_id=a1, batch_id=b1)
        r = client.get(
            "/v1/reconciliations",
            headers=auth_h,
            params={"status": "in_progress"},
        )
        ids = {item["id"] for item in r.json()["items"]}
        assert r1 in ids

    def test_invalid_status_returns_422(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/reconciliations", headers=auth_h, params={"status": "BOGUS"})
        assert r.status_code == 422

    def test_unknown_account_returns_empty_not_404(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        r = client.get(
            "/v1/reconciliations",
            headers=auth_h,
            params={"account_id": str(uuid4())},
        )
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_tenant_scoping(self, client: TestClient):
        # Household A.
        client.post(
            "/v1/auth/register",
            json={
                "email": "a@x.com",
                "password": "long-enough-password",
                "display_name": "A",
                "household_name": "A",
            },
        )
        a_token = client.post(
            "/v1/auth/login",
            json={"email": "a@x.com", "password": "long-enough-password"},
        ).json()["access_token"]
        a_h = {"Authorization": f"Bearer {a_token}"}
        a_acct = _create_account(client, a_h, "1110", "Checking")
        a_batch = _create_batch(client, a_h, a_acct)
        a_recon = _create_recon(client, a_h, account_id=a_acct, batch_id=a_batch)

        # Household B.
        client.post(
            "/v1/auth/register",
            json={
                "email": "b@y.com",
                "password": "long-enough-password",
                "display_name": "B",
                "household_name": "B",
            },
        )
        b_token = client.post(
            "/v1/auth/login",
            json={"email": "b@y.com", "password": "long-enough-password"},
        ).json()["access_token"]
        b_h = {"Authorization": f"Bearer {b_token}"}

        # B can't see A's reconciliation.
        r = client.get("/v1/reconciliations", headers=b_h)
        ids = {item["id"] for item in r.json()["items"]}
        assert a_recon not in ids

    def test_unauthenticated_returns_401(self, client: TestClient):
        r = client.get("/v1/reconciliations")
        assert_problem(r, status=401, code="auth.unauthorized")
