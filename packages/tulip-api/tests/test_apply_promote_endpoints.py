"""Tests for POST /v1/imports/{id}/apply + POST .../lines/{id}/promote (P5.4.a)."""

from __future__ import annotations

from pathlib import Path

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
def parsed_batch(
    client: TestClient,
    auth_h: dict[str, str],
    checking_account: str,
) -> str:
    body = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
    r = client.post(
        "/v1/imports",
        headers=auth_h,
        files={"file": ("x.ofx", body, "application/x-ofx")},
        data={"account_id": checking_account, "source_format": "ofx"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---- /apply --------------------------------------------------------------


class TestApplyImportHappyPath:
    def test_promotes_all_lines_and_returns_summary(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        r = client.post(f"/v1/imports/{parsed_batch}/apply", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["batch_id"] == parsed_batch
        assert body["status"] == "applied"
        assert body["created_count"] == 2  # minimal_ofx2.ofx has 2 lines
        assert body["skipped_count"] == 0
        assert len(body["transaction_ids"]) == 2

    def test_apply_flips_batch_status_to_applied(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        client.post(f"/v1/imports/{parsed_batch}/apply", headers=auth_h)
        r = client.get(f"/v1/imports/{parsed_batch}", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["status"] == "applied"
        assert r.json()["applied_at"] is not None

    def test_apply_no_categorize_succeeds_without_categorizer_account(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        """Slice B: ?no_categorize=true short-circuits the categorizer and
        auto-creates the Imbalance:Unknown account on demand. The endpoint
        must succeed even on a household with no chart-of-accounts entry
        for the categorizer's usual return value.
        """
        r = client.post(
            f"/v1/imports/{parsed_batch}/apply?no_categorize=true",
            headers=auth_h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created_count"] == 2
        # And the auto-created Imbalance:Unknown account exists.
        accounts = client.get("/v1/accounts", headers=auth_h).json()
        codes = {a.get("code") for a in accounts}
        assert "9999.USD" in codes


class TestApplyImportErrors:
    def test_unknown_batch_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        from uuid import uuid4

        r = client.post(f"/v1/imports/{uuid4()}/apply", headers=auth_h)
        assert_problem(r, status=404, code="import_batch.not_found")

    def test_already_applied_returns_409(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        client.post(f"/v1/imports/{parsed_batch}/apply", headers=auth_h)
        r = client.post(f"/v1/imports/{parsed_batch}/apply", headers=auth_h)
        assert_problem(r, status=409, code="import.already_applied")
        assert r.json()["batch_id"] == parsed_batch

    def test_unauthenticated_returns_401(self, client: TestClient, parsed_batch: str):
        r = client.post(f"/v1/imports/{parsed_batch}/apply")
        assert_problem(r, status=401, code="auth.unauthorized")


# ---- /lines/{line_id}/promote -------------------------------------------


def _first_line_id(client: TestClient, auth_h: dict[str, str], batch_id: str) -> str:
    r = client.get(f"/v1/imports/{batch_id}", headers=auth_h)
    return r.json()["lines"][0]["id"]


class TestPromoteLineHappyPath:
    def test_promotes_one_line_returns_201(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        line_id = _first_line_id(client, auth_h, parsed_batch)
        r = client.post(
            f"/v1/imports/{parsed_batch}/lines/{line_id}/promote",
            headers=auth_h,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["statement_line_id"] == line_id
        assert body["transaction_id"]


class TestPromoteLineErrors:
    def test_already_promoted_returns_409(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        line_id = _first_line_id(client, auth_h, parsed_batch)
        client.post(f"/v1/imports/{parsed_batch}/lines/{line_id}/promote", headers=auth_h)
        r = client.post(f"/v1/imports/{parsed_batch}/lines/{line_id}/promote", headers=auth_h)
        assert_problem(r, status=409, code="import.line.already_promoted")
        assert r.json()["line_id"] == line_id

    def test_unknown_batch_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        from uuid import uuid4

        r = client.post(f"/v1/imports/{uuid4()}/lines/{uuid4()}/promote", headers=auth_h)
        assert_problem(r, status=404, code="import_batch.not_found")

    def test_unknown_line_returns_404(
        self, client: TestClient, auth_h: dict[str, str], parsed_batch: str
    ):
        from uuid import uuid4

        r = client.post(f"/v1/imports/{parsed_batch}/lines/{uuid4()}/promote", headers=auth_h)
        assert_problem(r, status=404, code="import.line.not_found")
