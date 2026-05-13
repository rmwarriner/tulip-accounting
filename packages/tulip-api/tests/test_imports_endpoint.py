"""Tests for POST /v1/imports + GET /v1/imports/{id} (P5.2.a)."""

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


def _upload(
    client: TestClient,
    auth_h: dict[str, str],
    account_id: str,
    *,
    fixture: str = "minimal_ofx2.ofx",
    content_type: str = "application/x-ofx",
):
    body_bytes = (_OFX_FIXTURES / fixture).read_bytes()
    return client.post(
        "/v1/imports",
        headers=auth_h,
        files={"file": (fixture, body_bytes, content_type)},
        data={"account_id": account_id, "source_format": "ofx"},
    )


class TestUploadHappyPath:
    def test_uploads_ofx2_and_persists_lines(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        r = _upload(client, auth_h, checking_account)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["statement_line_count"] == 2
        assert body["imported_count"] == 2
        assert body["status"] == "parsed"
        assert body["source_format"] == "ofx"
        batch_id = body["id"]

        # GET returns the same data + the line list.
        r2 = client.get(f"/v1/imports/{batch_id}", headers=auth_h)
        assert r2.status_code == 200, r2.text
        full = r2.json()
        assert len(full["lines"]) == 2
        # Source-file order preserved.
        assert [line["line_number"] for line in full["lines"]] == [1, 2]
        # FITIDs round-tripped from parser to API.
        fitids = {line["fitid"] for line in full["lines"]}
        assert fitids == {"FITID-AMAZON-001", "FITID-PAYCHECK-001"}

    def test_uploads_ofx1_sgml(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        r = _upload(client, auth_h, checking_account, fixture="minimal_ofx1.sgml")
        assert r.status_code == 201, r.text
        assert r.json()["statement_line_count"] == 1


class TestUploadErrorPaths:
    def test_duplicate_file_returns_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        first = _upload(client, auth_h, checking_account)
        assert first.status_code == 201
        existing_id = first.json()["id"]

        second = _upload(client, auth_h, checking_account)
        body = assert_problem(second, code="import.duplicate_file", status=409)
        assert body["existing_batch_id"] == existing_id

    def test_unknown_account_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        bogus = "11111111-1111-1111-1111-111111111111"
        r = _upload(client, auth_h, bogus)
        assert_problem(r, code="account.unknown", status=400)

    def test_unsupported_content_type_returns_415(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        r = _upload(
            client,
            auth_h,
            checking_account,
            content_type="text/plain",
        )
        body = assert_problem(r, code="request.unsupported_media_type", status=415)
        assert body["received"] == "text/plain"

    def test_garbage_bytes_returns_parse_error(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={
                "file": ("not.ofx", b"not real ofx data", "application/x-ofx"),
            },
            data={"account_id": checking_account, "source_format": "ofx"},
        )
        assert_problem(r, code="import.ofx_parse_failed", status=400)

    def test_unauthenticated_returns_401(
        self,
        client: TestClient,
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
        r = client.post(
            "/v1/imports",
            files={"file": ("may.ofx", body_bytes, "application/x-ofx")},
            data={"account_id": checking_account, "source_format": "ofx"},
        )
        assert r.status_code == 401

    def test_force_override_creates_second_batch(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        first = _upload(client, auth_h, checking_account)
        assert first.status_code == 201
        body_bytes = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
        second = client.post(
            "/v1/imports?force=true",
            headers=auth_h,
            files={"file": ("may.ofx", body_bytes, "application/x-ofx")},
            data={"account_id": checking_account, "source_format": "ofx"},
        )
        assert second.status_code == 201, second.text
        assert second.json()["id"] != first.json()["id"]

    def test_force_override_rejected_for_member_role(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        session_maker,
    ):
        """force=true is admin-only — a member can upload but cannot force a duplicate (#230)."""
        from uuid import uuid4

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import User, UserRole

        # First admin upload establishes the duplicate.
        first = _upload(client, auth_h, checking_account)
        assert first.status_code == 201

        # Provision a member in the same household.
        admin_user_row = client.get("/v1/system/diagnostics").json()  # warm pre-auth
        from sqlalchemy import select

        from tulip_storage.models import User as UserModel

        with session_maker() as s:
            admin_row = s.execute(
                select(UserModel).where(UserModel.email == "admin@example.com")
            ).scalar_one()
            household_id = admin_row.household_id
            s.add(
                User(
                    household_id=household_id,
                    id=uuid4(),
                    email="member@example.com",
                    password_hash=hash_password("correct horse battery staple"),
                    display_name="Member",
                    role=UserRole.MEMBER,
                )
            )
            s.commit()
        del admin_user_row  # silence unused

        member_token = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "correct horse battery staple"},
        ).json()["access_token"]
        member_h = {"Authorization": f"Bearer {member_token}"}

        # Member with force=true → 403 auth.forbidden.
        body_bytes = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
        r = client.post(
            "/v1/imports?force=true",
            headers=member_h,
            files={"file": ("may.ofx", body_bytes, "application/x-ofx")},
            data={"account_id": checking_account, "source_format": "ofx"},
        )
        assert_problem(r, code="auth.forbidden", status=403)


class TestUploadQif:
    def test_uploads_qif_and_persists_lines(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "minimal.qif").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("may.qif", body_bytes, "application/qif")},
            data={"account_id": checking_account, "source_format": "qif"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["statement_line_count"] == 3
        assert body["source_format"] == "qif"

        r2 = client.get(f"/v1/imports/{body['id']}", headers=auth_h)
        assert r2.status_code == 200
        full = r2.json()
        # Currency on every line picks up the account's USD; QIF carries none.
        assert all(line["currency"] == "USD" for line in full["lines"])

    def test_qif_garbage_returns_parse_error(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={
                "file": ("not.qif", b"this is not qif data", "application/qif"),
            },
            data={"account_id": checking_account, "source_format": "qif"},
        )
        assert_problem(r, code="import.qif_parse_failed", status=400)

    def test_unsupported_format_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
        # 'journal' is reserved in the storage enum but no parser ships
        # for it yet — it's the canonical "unimplemented format" probe.
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("file.journal", body_bytes, "text/plain")},
            data={"account_id": checking_account, "source_format": "journal"},
        )
        body = assert_problem(r, code="import.unsupported_format", status=400)
        assert body["format"] == "journal"


class TestUploadCsv:
    @pytest.fixture
    def chase_profile_id(self, client: TestClient, auth_h: dict[str, str]) -> str:
        r = client.post(
            "/v1/imports/profiles",
            headers=auth_h,
            json={
                "name": "chase-checking",
                "date_column": "Posting Date",
                "date_format": "%m/%d/%Y",
                "amount_column": "Amount",
                "description_column": "Description",
                "reference_column": "Check or Slip #",
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_uploads_csv_with_profile(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        chase_profile_id: str,
    ):
        body_bytes = (_OFX_FIXTURES.parent / "csv" / "chase_checking.csv").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("may.csv", body_bytes, "text/csv")},
            data={
                "account_id": checking_account,
                "source_format": "csv",
                "profile_id": chase_profile_id,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["statement_line_count"] == 4
        assert body["source_format"] == "csv"

    def test_csv_without_profile_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES.parent / "csv" / "chase_checking.csv").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("may.csv", body_bytes, "text/csv")},
            data={"account_id": checking_account, "source_format": "csv"},
        )
        assert_problem(r, code="import.csv_profile_missing", status=400)

    def test_csv_with_unknown_profile_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES.parent / "csv" / "chase_checking.csv").read_bytes()
        bogus = "11111111-1111-1111-1111-111111111111"
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("may.csv", body_bytes, "text/csv")},
            data={
                "account_id": checking_account,
                "source_format": "csv",
                "profile_id": bogus,
            },
        )
        assert_problem(r, code="csv_profile.not_found", status=404)

    def test_csv_parse_error_surfaces_row_number(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        chase_profile_id: str,
    ):
        body = (
            b"Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
            b"05/12/2026,Good,-10.00,D,0,\n"
            b"13/45/2026,Bad,-20.00,D,0,\n"
        )
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("bad.csv", body, "text/csv")},
            data={
                "account_id": checking_account,
                "source_format": "csv",
                "profile_id": chase_profile_id,
            },
        )
        problem = assert_problem(r, code="import.csv_parse_failed", status=400)
        assert "row 2" in problem["detail"]


class TestGetImport:
    def test_unknown_id_returns_404(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        bogus = "11111111-1111-1111-1111-111111111111"
        r = client.get(f"/v1/imports/{bogus}", headers=auth_h)
        assert_problem(r, code="import_batch.not_found", status=404)

    def test_unauthenticated_returns_401(
        self,
        client: TestClient,
    ):
        bogus = "11111111-1111-1111-1111-111111111111"
        r = client.get(f"/v1/imports/{bogus}")
        assert r.status_code == 401


class TestListImports:
    """``GET /v1/imports`` — list batches in the caller's household."""

    def test_empty_household_returns_empty_list(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        r = client.get("/v1/imports", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == []
        assert body["next_cursor"] is None

    def test_unauthenticated_returns_401(self, client: TestClient):
        r = client.get("/v1/imports")
        assert r.status_code == 401

    def test_single_batch_round_trips(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        upload = _upload(client, auth_h, checking_account)
        assert upload.status_code == 201, upload.text
        batch_id = upload.json()["id"]

        r = client.get("/v1/imports", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["id"] == batch_id
        assert item["account_id"] == checking_account
        assert item["source_format"] == "ofx"
        assert item["status"] == "parsed"
        assert item["imported_count"] == 2
        assert item["skipped_count"] == 0
        assert "created_at" in item
        # The list shape intentionally omits ``lines`` — that's the show
        # endpoint's concern.
        assert "lines" not in item
        assert body["next_cursor"] is None

    def test_sorted_newest_first(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        first = _upload(client, auth_h, checking_account, fixture="minimal_ofx2.ofx")
        assert first.status_code == 201
        second = _upload(client, auth_h, checking_account, fixture="minimal_ofx1.sgml")
        assert second.status_code == 201
        first_id = first.json()["id"]
        second_id = second.json()["id"]

        r = client.get("/v1/imports", headers=auth_h)
        assert r.status_code == 200
        items = r.json()["items"]
        assert [i["id"] for i in items] == [second_id, first_id]

    def test_pagination_via_limit_and_after(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        a = _upload(client, auth_h, checking_account, fixture="minimal_ofx2.ofx").json()["id"]
        b = _upload(client, auth_h, checking_account, fixture="minimal_ofx1.sgml").json()["id"]

        page1 = client.get("/v1/imports", headers=auth_h, params={"limit": 1}).json()
        assert [i["id"] for i in page1["items"]] == [b]
        assert page1["next_cursor"] is not None

        page2 = client.get(
            "/v1/imports",
            headers=auth_h,
            params={"limit": 1, "after": page1["next_cursor"]},
        ).json()
        assert [i["id"] for i in page2["items"]] == [a]
        # With keyset pagination, ``len == limit`` always returns a cursor;
        # the next fetch confirms there are no more rows.
        assert page2["next_cursor"] is not None
        page3 = client.get(
            "/v1/imports",
            headers=auth_h,
            params={"limit": 1, "after": page2["next_cursor"]},
        ).json()
        assert page3["items"] == []
        assert page3["next_cursor"] is None

    def test_filter_by_status(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        upload = _upload(client, auth_h, checking_account)
        assert upload.status_code == 201
        # status=applied should currently match nothing.
        empty = client.get(
            "/v1/imports",
            headers=auth_h,
            params={"status": "applied"},
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        # status=parsed matches the freshly-uploaded batch.
        parsed = client.get(
            "/v1/imports",
            headers=auth_h,
            params={"status": "parsed"},
        )
        assert parsed.status_code == 200
        assert len(parsed.json()["items"]) == 1

    def test_filter_by_account(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        # Second account in the same household.
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Savings", "type": "asset", "currency": "USD", "code": "1115"},
        )
        savings_id = r.json()["id"]

        _upload(client, auth_h, checking_account, fixture="minimal_ofx2.ofx")
        _upload(client, auth_h, savings_id, fixture="minimal_ofx1.sgml")

        only_savings = client.get(
            "/v1/imports",
            headers=auth_h,
            params={"account_id": savings_id},
        )
        assert only_savings.status_code == 200
        items = only_savings.json()["items"]
        assert len(items) == 1
        assert items[0]["account_id"] == savings_id

    def test_invalid_status_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        # FastAPI's regex pattern validation surfaces as 422 validation.failed.
        r = client.get("/v1/imports", headers=auth_h, params={"status": "bogus"})
        assert r.status_code == 422

    def test_invalid_cursor_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        r = client.get("/v1/imports", headers=auth_h, params={"after": "not-base64!"})
        assert r.status_code == 422

    def test_limit_out_of_range_returns_422(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        r = client.get("/v1/imports", headers=auth_h, params={"limit": 0})
        assert r.status_code == 422
        r2 = client.get("/v1/imports", headers=auth_h, params={"limit": 1000})
        assert r2.status_code == 422

    def test_tenant_isolation(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        # Upload a batch for the existing admin household.
        _upload(client, auth_h, checking_account)

        # Register a second household; the new caller must see zero batches.
        client.post(
            "/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "another long password value",
                "display_name": "Other",
                "household_name": "Doe",
            },
        )
        other_login = client.post(
            "/v1/auth/login",
            json={"email": "other@example.com", "password": "another long password value"},
        )
        other_token = other_login.json()["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}

        r = client.get("/v1/imports", headers=other_headers)
        assert r.status_code == 200
        assert r.json()["items"] == []
