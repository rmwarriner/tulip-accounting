"""Integration tests for /v1/sinking-funds (P4.1.b).

Mirror of the envelope tests minus refill, plus contribution-strategy
validation that's specific to sinking funds.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

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


def _future_date() -> str:
    return date(date.today().year + 1, 1, 1).isoformat()


class TestCreateSinkingFund:
    def test_minimal_manual_succeeds(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000.00",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Vacation"
        assert body["target_amount"] == "3000.00"
        assert body["contribution_strategy"] == "manual"
        assert body["contribution_amount"] is None

    def test_manual_with_contribution_amount(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000.00",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
                "contribution_amount": "250.00",
            },
        )
        assert r.status_code == 201
        assert r.json()["contribution_amount"] == "250.00"

    def test_invalid_strategy_rejected(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Bad",
                "currency": "USD",
                "target_amount": "1000",
                "target_date": _future_date(),
                "contribution_strategy": "weird",
            },
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_zero_target_amount_rejected(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Bad",
                "currency": "USD",
                "target_amount": "0",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        )
        assert_problem(r, code="validation.failed", status=422)


class TestListAndGetSinkingFund:
    def test_list_empty(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/sinking-funds", headers=auth_h)
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_created(self, client: TestClient, auth_h: dict[str, str]):
        client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        )
        body = client.get("/v1/sinking-funds", headers=auth_h).json()
        assert len(body) == 1
        assert body[0]["name"] == "Vacation"

    def test_get_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get(f"/v1/sinking-funds/{uuid4()}", headers=auth_h)
        assert_problem(r, code="sinking_fund.not_found", status=404)


class TestUpdateSinkingFund:
    def test_patch_target_amount_and_date(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        ).json()
        new_date = date(date.today().year + 2, 6, 1).isoformat()
        r = client.patch(
            f"/v1/sinking-funds/{created['id']}",
            headers=auth_h,
            json={"target_amount": "5000", "target_date": new_date},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_amount"] == "5000"
        assert body["target_date"] == new_date


class TestDeactivateSinkingFund:
    def test_delete_marks_inactive(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        ).json()
        r = client.delete(f"/v1/sinking-funds/{created['id']}", headers=auth_h)
        assert r.status_code == 200
        # Honest body: DELETE deactivates, it doesn't erase (#236).
        assert r.json() == {"action": "deactivated", "data_retained": ["name"]}
        assert client.get("/v1/sinking-funds", headers=auth_h).json() == []

    def test_redact_sinking_fund_renames_pool(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ):
        """#236: redact replaces a deactivated sinking fund's pool name with a placeholder."""
        from sqlalchemy import select

        from tulip_storage.models import AllocationPool

        created = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Sensitive Fund",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        ).json()
        client.delete(f"/v1/sinking-funds/{created['id']}", headers=auth_h).raise_for_status()
        r = client.post(f"/v1/sinking-funds/{created['id']}/redact", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.json() == {"action": "redacted", "fields_redacted": ["name"]}
        with session_maker() as s:
            pool = s.execute(
                select(AllocationPool).where(AllocationPool.id == UUID(created["id"]))
            ).scalar_one()
        assert pool.name != "Sensitive Fund"
        assert pool.name.startswith("redacted-sinking-fund-")

    def test_redact_active_sinking_fund_returns_409(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        created = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Still Active",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        ).json()
        r = client.post(f"/v1/sinking-funds/{created['id']}/redact", headers=auth_h)
        assert_problem(r, code="sinking_fund.not_redactable", status=409)

    def test_redact_unknown_sinking_fund_returns_404(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        r = client.post(f"/v1/sinking-funds/{uuid4()}/redact", headers=auth_h)
        assert_problem(r, code="sinking_fund.not_found", status=404)

    def test_redact_requires_auth(self, client: TestClient):
        r = client.post(f"/v1/sinking-funds/{uuid4()}/redact")
        assert r.status_code == 401


class TestSinkingFundBalance:
    def test_balance_zero_for_new(self, client: TestClient, auth_h: dict[str, str]):
        sf = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": _future_date(),
                "contribution_strategy": "manual",
            },
        ).json()
        r = client.get(f"/v1/sinking-funds/{sf['id']}/balance", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pool_id"] == sf["id"]
        assert Decimal(body["balance"]) == Decimal("0.00")

    def test_balance_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get(f"/v1/sinking-funds/{uuid4()}/balance", headers=auth_h)
        assert_problem(r, code="sinking_fund.not_found", status=404)


class TestSinkingFundUnauthenticated:
    def test_list_without_token_returns_401(self, client: TestClient):
        r = client.get("/v1/sinking-funds")
        assert r.status_code == 401
