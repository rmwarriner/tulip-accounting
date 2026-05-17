"""Tests for /v1/imports/profiles CRUD + YAML import/export (P5.2.c)."""

from __future__ import annotations

import pytest
import yaml
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


_VALID_PAYLOAD = {
    "name": "chase-checking",
    "date_column": "Posting Date",
    "date_format": "%m/%d/%Y",
    "amount_column": "Amount",
    "description_column": "Description",
}


class TestCreate:
    def test_create_returns_201_and_full_body(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "chase-checking"
        assert body["amount_negative_means"] == "debit"  # default
        assert "id" in body
        assert "created_at" in body

    def test_duplicate_name_returns_409(self, client: TestClient, auth_h: dict[str, str]):
        client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)
        r = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)
        body = assert_problem(r, code="csv_profile.duplicate_name", status=409)
        assert body["name"] == "chase-checking"

    def test_validation_failure_returns_422(self, client: TestClient, auth_h: dict[str, str]):
        bad = {**_VALID_PAYLOAD, "amount_negative_means": "neither"}
        r = client.post("/v1/imports/profiles", headers=auth_h, json=bad)
        assert_problem(r, code="validation.failed", status=422)

    def test_unauthenticated_returns_401(self, client: TestClient):
        r = client.post("/v1/imports/profiles", json=_VALID_PAYLOAD)
        assert r.status_code == 401


class TestList:
    def test_list_scopes_to_household(self, client: TestClient, auth_h: dict[str, str]):
        client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)

        # Second household; should not see the first's profile.
        client.post(
            "/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "correct horse battery staple",
                "display_name": "Other",
                "household_name": "Jones",
            },
        )
        r = client.post(
            "/v1/auth/login",
            json={
                "email": "other@example.com",
                "password": "correct horse battery staple",
            },
        )
        other_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        listing = client.get("/v1/imports/profiles", headers=other_h)
        assert listing.status_code == 200
        assert listing.json() == []

        # First household sees their profile.
        listing1 = client.get("/v1/imports/profiles", headers=auth_h)
        assert len(listing1.json()) == 1


class TestGet:
    def test_get_by_uuid(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD).json()
        r = client.get(f"/v1/imports/profiles/{created['id']}", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_by_name(self, client: TestClient, auth_h: dict[str, str]):
        client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)
        r = client.get("/v1/imports/profiles/chase-checking", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["name"] == "chase-checking"

    def test_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/imports/profiles/no-such-name", headers=auth_h)
        assert_problem(r, code="csv_profile.not_found", status=404)


class TestPatch:
    def test_partial_update(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD).json()
        r = client.patch(
            f"/v1/imports/profiles/{created['id']}",
            headers=auth_h,
            json={"date_format": "%Y-%m-%d"},
        )
        assert r.status_code == 200
        assert r.json()["date_format"] == "%Y-%m-%d"
        # Unchanged fields preserved.
        assert r.json()["amount_column"] == "Amount"

    def test_rename_propagates_to_get_by_name(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD).json()
        client.patch(
            f"/v1/imports/profiles/{created['id']}",
            headers=auth_h,
            json={"name": "chase-renamed"},
        )
        r = client.get("/v1/imports/profiles/chase-renamed", headers=auth_h)
        assert r.status_code == 200
        # Old name no longer resolves.
        old = client.get("/v1/imports/profiles/chase-checking", headers=auth_h)
        assert old.status_code == 404

    def test_rename_to_existing_name_returns_409(self, client: TestClient, auth_h: dict[str, str]):
        first = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD).json()
        second = client.post(
            "/v1/imports/profiles",
            headers=auth_h,
            json={**_VALID_PAYLOAD, "name": "amex"},
        ).json()
        r = client.patch(
            f"/v1/imports/profiles/{second['id']}",
            headers=auth_h,
            json={"name": first["name"]},
        )
        assert_problem(r, code="csv_profile.duplicate_name", status=409)


class TestDelete:
    def test_delete_then_404(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD).json()
        r = client.delete(f"/v1/imports/profiles/{created['id']}", headers=auth_h)
        assert r.status_code == 204
        r2 = client.get(f"/v1/imports/profiles/{created['id']}", headers=auth_h)
        assert_problem(r2, code="csv_profile.not_found", status=404)


class TestExportImport:
    def test_export_returns_yaml(self, client: TestClient, auth_h: dict[str, str]):
        client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)
        r = client.get("/v1/imports/profiles/chase-checking/export", headers=auth_h)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-yaml")
        loaded = yaml.safe_load(r.text)
        assert loaded["name"] == "chase-checking"

    def test_round_trip_export_import(self, client: TestClient, auth_h: dict[str, str]):
        # Export from household A, delete locally, re-import from YAML.
        client.post("/v1/imports/profiles", headers=auth_h, json=_VALID_PAYLOAD)
        exported = client.get("/v1/imports/profiles/chase-checking/export", headers=auth_h).text
        client.delete("/v1/imports/profiles/chase-checking", headers=auth_h)

        r = client.post(
            "/v1/imports/profiles/import",
            headers={**auth_h, "content-type": "application/x-yaml"},
            content=exported.encode(),
        )
        assert r.status_code == 201, r.text
        assert r.json()["name"] == "chase-checking"

    def test_import_unsafe_yaml_rejected(self, client: TestClient, auth_h: dict[str, str]):
        unsafe_tag = "!!python/object/apply:builtins.int"
        unsafe = (
            "name: pwned\n"
            "date_column: D\n"
            "date_format: '%Y-%m-%d'\n"
            "amount_column: A\n"
            "description_column: X\n"
            f"skip_header_rows: {unsafe_tag} [42]\n"
        ).encode()
        r = client.post(
            "/v1/imports/profiles/import",
            headers={**auth_h, "content-type": "application/x-yaml"},
            content=unsafe,
        )
        assert_problem(r, code="csv_profile.invalid_yaml", status=400)

    def test_import_garbage_yaml_returns_400(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/imports/profiles/import",
            headers={**auth_h, "content-type": "application/x-yaml"},
            content=b"name: : :\n  bad indent",
        )
        assert_problem(r, code="csv_profile.invalid_yaml", status=400)


class TestImportSizeCap:
    """#351 / security audit L-11: 100 KB cap matches THREAT_MODEL.md §5.2
    and defends against a yaml.safe_load that allocates aggressively on
    degenerate input.
    """

    def test_oversize_yaml_returns_413(self, client: TestClient, auth_h: dict[str, str]):
        oversize = ("name: x\n" + ("# pad\n" * 30_000)).encode()  # >150 KB
        r = client.post(
            "/v1/imports/profiles/import",
            headers={**auth_h, "content-type": "application/x-yaml"},
            content=oversize,
        )
        assert_problem(r, code="request.payload_too_large", status=413)
