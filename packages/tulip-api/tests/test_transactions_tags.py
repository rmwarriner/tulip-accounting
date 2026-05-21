"""Tests for the v1 transaction-tags surface (#39).

Covers POST/GET/PATCH plumbing and the `?tag=foo` filter on the list
endpoint. Validation lives in the repo (`TransactionTagRepository`)
and surfaces as a 400 `tag.invalid` Problem Detail.
"""

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


def _lunch_body(cash: str, food: str, tags: list[str] | None = None) -> dict:
    body = {
        "date": date.today().isoformat(),
        "description": "Lunch",
        "postings": [
            {"account_id": food, "amount": "12.50", "currency": "USD"},
            {"account_id": cash, "amount": "-12.50", "currency": "USD"},
        ],
    }
    if tags is not None:
        body["tags"] = tags
    return body


class TestTagsRoundTrip:
    def test_create_with_tags_stores_them(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["work", "client/acme"]),
        )
        assert r.status_code == 201, r.text
        assert sorted(r.json()["tags"]) == ["client/acme", "work"]

    def test_create_without_tags_returns_empty_list(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        r = client.post("/v1/transactions", headers=auth_h, json=_lunch_body(cash, food))
        assert r.status_code == 201
        assert r.json()["tags"] == []

    def test_create_normalises_case_and_dedupes(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["Work", "WORK", "work"]),
        )
        assert r.status_code == 201
        assert r.json()["tags"] == ["work"]

    def test_get_returns_tags(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        created = client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["reimbursable"]),
        ).json()
        fetched = client.get(f"/v1/transactions/{created['id']}", headers=auth_h).json()
        assert fetched["tags"] == ["reimbursable"]

    def test_list_returns_tags_per_row(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["work"]),
        )
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["personal"]),
        )
        rows = client.get("/v1/transactions", headers=auth_h).json()
        # Two rows; each carries its own tag set.
        tag_sets = sorted(tuple(sorted(r["tags"])) for r in rows)
        assert tag_sets == [("personal",), ("work",)]


class TestPatchPendingOnly:
    """Tag changes via PATCH inherit the PENDING-only constraint (#39 v1).

    The PATCH endpoint already returns 409 ``transaction.not_editable``
    for POSTED / RECONCILED. Tags ride that same gate — users wanting
    to edit tags on a POSTED transaction go through ``/replace``
    (#209a/b) the same way they'd edit any other field. A dedicated
    tag-only PATCH endpoint that bypasses the status check is a
    candidate follow-up if usage warrants.

    Since the test harness can only create POSTED transactions through
    POST /v1/transactions, all we can verify here is that PATCH'ing
    tags on a POSTED transaction returns 409 (the body's wiring is
    exercised; the success-path coverage rides on the existing
    PATCH-PENDING tests + the tag-on-POST + tag-via-/replace tests).
    """

    def test_patch_tags_on_posted_returns_not_editable(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        created = client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["work"]),
        ).json()
        r = client.patch(
            f"/v1/transactions/{created['id']}",
            headers=auth_h,
            json={"tags": ["audited"]},
        )
        assert r.status_code == 409
        # Tags are unchanged.
        fetched = client.get(f"/v1/transactions/{created['id']}", headers=auth_h).json()
        assert fetched["tags"] == ["work"]


class TestListFilter:
    def test_filter_matches_only_tagged_transactions(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        tagged = client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["work"]),
        ).json()
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["personal"]),
        )
        # No tag → both come back.
        rows = client.get("/v1/transactions", headers=auth_h).json()
        assert len(rows) == 2
        # ?tag=work → only the work-tagged one.
        rows = client.get("/v1/transactions", params={"tag": "work"}, headers=auth_h).json()
        assert [r["id"] for r in rows] == [tagged["id"]]

    def test_filter_is_case_insensitive(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["work"]),
        )
        rows = client.get("/v1/transactions", params={"tag": "WORK"}, headers=auth_h).json()
        assert len(rows) == 1

    def test_filter_with_no_matches_returns_empty(
        self, client: TestClient, auth_h: dict[str, str], cash_and_food: tuple[str, str]
    ):
        cash, food = cash_and_food
        client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=["work"]),
        )
        rows = client.get("/v1/transactions", params={"tag": "nonexistent"}, headers=auth_h).json()
        assert rows == []


class TestValidation:
    @pytest.mark.parametrize(
        "bad_tag",
        ["", "   ", "x" * 65, "has space", "embedded\nnewline", "control\x00char"],
    )
    def test_create_with_invalid_tag_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        bad_tag: str,
    ):
        cash, food = cash_and_food
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json=_lunch_body(cash, food, tags=[bad_tag]),
        )
        assert r.status_code == 400, r.text
        assert_problem(r, code="tag.invalid", status=400)

    def test_filter_with_invalid_tag_rejected(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/transactions", params={"tag": "has space"}, headers=auth_h)
        assert r.status_code == 400
        assert_problem(r, code="tag.invalid", status=400)


# ---- ADR-0009 PR C: effective-tags endpoint --------------------------------


class TestEffectiveTagsEndpoint:
    """``GET /v1/transactions/{id}/effective-tags`` returns direct + inherited."""

    def _seed_tx_with_tags(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        *,
        tx_tags: list[str],
        account_tags: list[str],
    ) -> str:
        """Create accounts + a tagged transaction; return the tx id."""
        cash = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Cash", "type": "asset", "currency": "USD"},
        ).json()
        food = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Food",
                "type": "expense",
                "currency": "USD",
                "tags": account_tags,
            },
        ).json()
        tx = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": "2026-05-01",
                "description": "Lunch",
                "tags": tx_tags,
                "postings": [
                    {"account_id": food["id"], "amount": "10.00", "currency": "USD"},
                    {"account_id": cash["id"], "amount": "-10.00", "currency": "USD"},
                ],
            },
        )
        assert tx.status_code == 201, tx.text
        return tx.json()["id"]

    def test_returns_direct_and_inherited_sets(self, client: TestClient, auth_h: dict[str, str]):
        tx_id = self._seed_tx_with_tags(
            client,
            auth_h,
            tx_tags=["birthday"],
            account_tags=["essential"],
        )
        r = client.get(f"/v1/transactions/{tx_id}/effective-tags", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["direct"] == ["birthday"]
        assert set(body["effective"]) == {"birthday", "essential"}
        # by_provenance carries every edge, distinguishable.
        provenances = {(p["name"], p["provenance"]) for p in body["by_provenance"]}
        assert ("birthday", "transaction") in provenances
        assert ("essential", "account") in provenances

    def test_returns_empty_for_unknown_transaction(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        from uuid import uuid4

        r = client.get(f"/v1/transactions/{uuid4()}/effective-tags", headers=auth_h)
        assert r.status_code == 404
