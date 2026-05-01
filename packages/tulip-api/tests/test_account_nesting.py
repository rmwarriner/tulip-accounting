"""Parent-account validation: cycle / type / currency / visibility rules.

#42 enforces four consistency rules whenever ``parent_account_id`` is
set or changed:

1. **No cycles.** On PATCH, the proposed new parent cannot be a
   descendant of the account being updated.
2. **Type match.** ``parent.type`` must equal ``child.type``.
3. **Currency match.** ``parent.currency`` must equal ``child.currency``.
   (#44 tracks deliberate relaxation for travel-style multi-currency
   hierarchies.)
4. **Visibility.** ``child.visibility ≤ parent.visibility`` — a private
   parent forces children to be private; a shared parent permits either.

The parent must also exist in this household and be active. An inactive
parent rejects with the same ``account.parent_not_found`` problem an
absent parent does.
"""

from __future__ import annotations

from uuid import uuid4

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
    return str(r.json()["access_token"])


@pytest.fixture
def auth_h(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def _create(
    client: TestClient,
    auth_h: dict[str, str],
    *,
    name: str,
    type_: str = "asset",
    currency: str = "USD",
    visibility: str = "shared",
    code: str | None = None,
    parent_account_id: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "type": type_,
        "currency": currency,
        "visibility": visibility,
    }
    if code is not None:
        body["code"] = code
    if parent_account_id is not None:
        body["parent_account_id"] = parent_account_id
    r = client.post("/v1/accounts", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


class TestCreateWithParent:
    def test_happy_nested_create(self, client: TestClient, auth_h: dict[str, str]):
        parent = _create(client, auth_h, name="Assets", type_="asset")
        child = _create(
            client,
            auth_h,
            name="Checking",
            type_="asset",
            parent_account_id=str(parent["id"]),
        )
        assert child["parent_account_id"] == str(parent["id"])

    def test_unknown_parent_returns_problem(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Orphan",
                "type": "asset",
                "currency": "USD",
                "parent_account_id": str(uuid4()),
            },
        )
        assert_problem(r, code="account.parent_not_found", status=404)

    def test_inactive_parent_rejected(self, client: TestClient, auth_h: dict[str, str]):
        parent = _create(client, auth_h, name="Old", type_="asset")
        client.delete(f"/v1/accounts/{parent['id']}", headers=auth_h)

        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Child",
                "type": "asset",
                "currency": "USD",
                "parent_account_id": str(parent["id"]),
            },
        )
        assert_problem(r, code="account.parent_not_found", status=404)

    def test_type_mismatch_rejected(self, client: TestClient, auth_h: dict[str, str]):
        parent = _create(client, auth_h, name="Assets", type_="asset")
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Wrong type",
                "type": "expense",
                "currency": "USD",
                "parent_account_id": str(parent["id"]),
            },
        )
        body = assert_problem(r, code="account.parent_type_mismatch", status=400)
        assert "asset" in body["detail"].lower()
        assert "expense" in body["detail"].lower()

    def test_currency_mismatch_rejected(self, client: TestClient, auth_h: dict[str, str]):
        parent = _create(client, auth_h, name="USD parent", currency="USD")
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "EUR child",
                "type": "asset",
                "currency": "EUR",
                "parent_account_id": str(parent["id"]),
            },
        )
        body = assert_problem(r, code="account.parent_currency_mismatch", status=400)
        assert "USD" in body["detail"]
        assert "EUR" in body["detail"]

    def test_shared_child_under_private_parent_rejected(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        parent = _create(client, auth_h, name="Private parent", visibility="private")
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Shared child",
                "type": "asset",
                "currency": "USD",
                "visibility": "shared",
                "parent_account_id": str(parent["id"]),
            },
        )
        assert_problem(r, code="account.parent_visibility_violation", status=400)

    def test_private_child_under_private_parent_allowed(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        parent = _create(client, auth_h, name="Private parent", visibility="private")
        child = _create(
            client,
            auth_h,
            name="Private child",
            visibility="private",
            parent_account_id=str(parent["id"]),
        )
        assert child["visibility"] == "private"


class TestPatchReparent:
    def test_happy_reparent(self, client: TestClient, auth_h: dict[str, str]):
        first_parent = _create(client, auth_h, name="P1", type_="asset")
        second_parent = _create(client, auth_h, name="P2", type_="asset")
        child = _create(
            client,
            auth_h,
            name="C",
            type_="asset",
            parent_account_id=str(first_parent["id"]),
        )

        r = client.patch(
            f"/v1/accounts/{child['id']}",
            headers=auth_h,
            json={"parent_account_id": str(second_parent["id"])},
        )
        assert r.status_code == 200, r.text
        assert r.json()["parent_account_id"] == str(second_parent["id"])

    def test_self_parent_rejected_as_cycle(self, client: TestClient, auth_h: dict[str, str]):
        a = _create(client, auth_h, name="A", type_="asset")
        r = client.patch(
            f"/v1/accounts/{a['id']}",
            headers=auth_h,
            json={"parent_account_id": str(a["id"])},
        )
        assert_problem(r, code="account.parent_cycle", status=400)

    def test_descendant_as_parent_rejected_as_cycle(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        # Build a → b → c. Then try to reparent a under c (cycle).
        a = _create(client, auth_h, name="A", type_="asset")
        b = _create(
            client,
            auth_h,
            name="B",
            type_="asset",
            parent_account_id=str(a["id"]),
        )
        c = _create(
            client,
            auth_h,
            name="C",
            type_="asset",
            parent_account_id=str(b["id"]),
        )

        r = client.patch(
            f"/v1/accounts/{a['id']}",
            headers=auth_h,
            json={"parent_account_id": str(c["id"])},
        )
        assert_problem(r, code="account.parent_cycle", status=400)

    def test_patch_type_mismatch_rejected(self, client: TestClient, auth_h: dict[str, str]):
        asset_parent = _create(client, auth_h, name="A parent", type_="asset")
        expense_child = _create(client, auth_h, name="An expense", type_="expense")

        r = client.patch(
            f"/v1/accounts/{expense_child['id']}",
            headers=auth_h,
            json={"parent_account_id": str(asset_parent["id"])},
        )
        assert_problem(r, code="account.parent_type_mismatch", status=400)

    def test_patch_currency_mismatch_rejected(self, client: TestClient, auth_h: dict[str, str]):
        usd_parent = _create(client, auth_h, name="USD", currency="USD")
        eur_child = _create(client, auth_h, name="EUR child", currency="EUR")

        r = client.patch(
            f"/v1/accounts/{eur_child['id']}",
            headers=auth_h,
            json={"parent_account_id": str(usd_parent["id"])},
        )
        assert_problem(r, code="account.parent_currency_mismatch", status=400)

    def test_patch_visibility_violation_rejected(self, client: TestClient, auth_h: dict[str, str]):
        private_parent = _create(client, auth_h, name="Private", visibility="private")
        shared_child = _create(client, auth_h, name="Shared", visibility="shared")

        r = client.patch(
            f"/v1/accounts/{shared_child['id']}",
            headers=auth_h,
            json={"parent_account_id": str(private_parent["id"])},
        )
        assert_problem(r, code="account.parent_visibility_violation", status=400)

    def test_patch_can_omit_parent_to_leave_unchanged(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        parent = _create(client, auth_h, name="P", type_="asset")
        child = _create(
            client,
            auth_h,
            name="C",
            type_="asset",
            parent_account_id=str(parent["id"]),
        )
        r = client.patch(
            f"/v1/accounts/{child['id']}",
            headers=auth_h,
            json={"name": "Renamed"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Renamed"
        assert body["parent_account_id"] == str(parent["id"])
