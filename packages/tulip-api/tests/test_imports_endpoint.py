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

    def test_split_qif_persists_one_line_with_consolidated_total(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        session_maker,
    ):
        """#297: a 2-split QIF record produces ONE statement_line with the parent total
        + structured ``__splits__`` envelope in raw_json.
        """
        import json as _json
        from decimal import Decimal as _Decimal

        from sqlalchemy import select

        from tulip_storage.models import StatementLine

        body_bytes = (_OFX_FIXTURES.parent / "qif" / "split_gas_bill.qif").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("gas.qif", body_bytes, "application/qif")},
            data={"account_id": checking_account, "source_format": "qif"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["statement_line_count"] == 1

        # The GET endpoint doesn't expose raw_json (internal detail);
        # read the DB row directly for the split envelope check.
        with session_maker() as s:
            line = s.execute(select(StatementLine)).scalar_one()
        assert _Decimal(line.amount) == _Decimal("-58.99")
        assert line.currency == "USD"
        raw = _json.loads(line.raw_json)
        assert "__splits__" in raw
        assert len(raw["__splits__"]) == 2
        amounts = sorted(_Decimal(s["amount"]) for s in raw["__splits__"])
        assert amounts == [_Decimal("-45.27"), _Decimal("-13.72")]

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

    def test_multi_account_qif_without_selector_is_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        # #195: a multi-account QIF imported plainly would silently merge
        # every account into one — the API rejects it with the names so
        # the CLI can render a starter --account-map.
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account.qif").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={"account_id": checking_account, "source_format": "qif"},
        )
        body = assert_problem(r, code="import.multi_account_qif", status=400)
        assert body["account_names"] == ["Checking", "Credit Card", "Savings"]

    def test_multi_account_qif_with_selector_ingests_one_account(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        # qif_account picks one !Account block; just its transactions land.
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account.qif").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={
                "account_id": checking_account,
                "source_format": "qif",
                "qif_account": "Checking",
            },
        )
        assert r.status_code == 201, r.text
        # The Checking block has two records; Savings + Credit Card excluded.
        assert r.json()["statement_line_count"] == 2

    def test_qif_account_selector_not_in_file_is_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account.qif").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={
                "account_id": checking_account,
                "source_format": "qif",
                "qif_account": "Nonexistent",
            },
        )
        body = assert_problem(r, code="import.qif_account_not_found", status=400)
        assert body["qif_account"] == "Nonexistent"
        assert "Checking" in body["available"]

    def test_single_account_qif_unaffected_by_multi_account_guard(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        # A plain single-account QIF (no !Account blocks) still imports
        # with just account_id — no regression from the #195 guard.
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "minimal.qif").read_bytes()
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("may.qif", body_bytes, "application/qif")},
            data={"account_id": checking_account, "source_format": "qif"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["statement_line_count"] == 3

    def test_unsupported_format_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ):
        body_bytes = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
        # 'beancount' is a planned future format (#34) that has no parser
        # yet — it's the canonical "unimplemented format" probe.
        r = client.post(
            "/v1/imports",
            headers=auth_h,
            files={"file": ("file.beancount", body_bytes, "text/plain")},
            data={"account_id": checking_account, "source_format": "beancount"},
        )
        body = assert_problem(r, code="import.unsupported_format", status=400)
        assert body["format"] == "beancount"


class TestUploadMultiAccountQif:
    """POST /v1/imports/multi-account — split, per-account batches, transfer pairing (#195b)."""

    @staticmethod
    def _acct(client: TestClient, auth_h: dict[str, str], name: str, code: str) -> str:
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": name, "type": "asset", "currency": "USD", "code": code},
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def test_transfer_pair_lands_as_one_balanced_transaction(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        session_maker,
    ):
        import json
        from decimal import Decimal

        from sqlalchemy import select

        from tulip_storage.models import Posting, StatementLine, Transaction

        checking = self._acct(client, auth_h, "Checking", "1110")
        savings = self._acct(client, auth_h, "Savings", "1200")
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account_transfer.qif").read_bytes()

        r = client.post(
            "/v1/imports/multi-account",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={"account_map": json.dumps({"Checking": checking, "Savings": savings})},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["batches"]) == 2
        assert body["transfer_count"] == 1
        assert body["warnings"] == []

        with session_maker() as s:
            # Exactly one PENDING transaction — the paired transfer — with a
            # posting on each account that nets to zero.
            txns = list(s.execute(select(Transaction)).scalars())
            assert len(txns) == 1
            postings = list(
                s.execute(select(Posting).where(Posting.transaction_id == txns[0].id)).scalars()
            )
            assert len(postings) == 2
            amounts = sorted(p.amount for p in postings)
            assert amounts == [Decimal("-200.00"), Decimal("200.00")]
            assert {str(p.account_id) for p in postings} == {checking, savings}
            # Both transfer-leg statement lines are marked promoted to it.
            promoted = list(
                s.execute(
                    select(StatementLine).where(StatementLine.promoted_transaction_id == txns[0].id)
                ).scalars()
            )
            assert len(promoted) == 2

    def test_unpaired_transfer_leg_plugs_with_imbalance(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        session_maker,
    ):
        """#448: an unpaired transfer leg lands as a balanced PENDING
        transaction with an Imbalance:Unknown counter-posting. Operator
        re-targets later via reconciliation."""
        import json
        from decimal import Decimal

        from sqlalchemy import select

        from tulip_storage.models import Account, Posting, StatementLine, Transaction

        checking = self._acct(client, auth_h, "Checking", "1110")
        savings = self._acct(client, auth_h, "Savings", "1200")
        credit = self._acct(client, auth_h, "Credit Card", "2100")
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account.qif").read_bytes()

        r = client.post(
            "/v1/imports/multi-account",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={
                "account_map": json.dumps(
                    {"Checking": checking, "Savings": savings, "Credit Card": credit}
                )
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["transfer_count"] == 0
        assert len(body["warnings"]) == 1
        # Warning text reflects the new behaviour.
        assert "Imbalance:Unknown plug" in body["warnings"][0]

        with session_maker() as s:
            # Exactly one tx — the unpaired transfer's Imbalance plug.
            txns = list(s.execute(select(Transaction)).scalars())
            assert len(txns) == 1
            postings = list(
                s.execute(select(Posting).where(Posting.transaction_id == txns[0].id)).scalars()
            )
            assert len(postings) == 2
            # Postings net to zero.
            assert sum(p.amount for p in postings) == Decimal("0")
            # One of the postings lands on the auto-created Imbalance acct.
            imbalance = s.execute(select(Account).where(Account.code == "9999.USD")).scalar_one()
            assert any(p.account_id == imbalance.id for p in postings)
            # The source statement line is marked promoted to it.
            promoted = list(
                s.execute(
                    select(StatementLine).where(StatementLine.promoted_transaction_id == txns[0].id)
                ).scalars()
            )
            assert len(promoted) == 1

    def test_unmapped_account_is_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        import json

        checking = self._acct(client, auth_h, "Checking", "1110")
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account_transfer.qif").read_bytes()
        # Map omits "Savings".
        r = client.post(
            "/v1/imports/multi-account",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={"account_map": json.dumps({"Checking": checking})},
        )
        body = assert_problem(r, code="import.qif_account_unmapped", status=400)
        assert body["unmapped"] == ["Savings"]

    def test_invalid_account_map_json_is_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account_transfer.qif").read_bytes()
        r = client.post(
            "/v1/imports/multi-account",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={"account_map": "{not valid json"},
        )
        assert_problem(r, code="import.account_map_invalid", status=400)

    def test_single_account_qif_is_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        import json

        # minimal.qif has no !Account blocks — the wrong endpoint for it.
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "minimal.qif").read_bytes()
        r = client.post(
            "/v1/imports/multi-account",
            headers=auth_h,
            files={"file": ("single.qif", body_bytes, "application/qif")},
            data={"account_map": json.dumps({"Whatever": str(__import__("uuid").uuid4())})},
        )
        assert_problem(r, code="import.qif_parse_failed", status=400)

    def test_unknown_tulip_account_uuid_is_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        import json
        from uuid import uuid4

        checking = self._acct(client, auth_h, "Checking", "1110")
        body_bytes = (_OFX_FIXTURES.parent / "qif" / "multi_account_transfer.qif").read_bytes()
        r = client.post(
            "/v1/imports/multi-account",
            headers=auth_h,
            files={"file": ("multi.qif", body_bytes, "application/qif")},
            data={"account_map": json.dumps({"Checking": checking, "Savings": str(uuid4())})},
        )
        assert_problem(r, code="account.unknown", status=400)


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


class TestDeleteImportBatch:
    """#345: DELETE /v1/imports/{batch_id} admin-only, refuses if any
    line is already promoted to a ledger transaction.
    """

    def test_admin_can_delete_unpromoted_batch(
        self, client: TestClient, auth_h: dict[str, str], checking_account: str
    ) -> None:
        upload = _upload(client, auth_h, checking_account).json()
        batch_id = upload["id"]
        r = client.delete(f"/v1/imports/{batch_id}", headers=auth_h)
        assert r.status_code == 204, r.text
        # Subsequent GET should 404.
        follow = client.get(f"/v1/imports/{batch_id}", headers=auth_h)
        assert follow.status_code == 404
        assert follow.json()["code"] == "import_batch.not_found"

    def test_delete_cascades_statement_lines(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        session_maker,
    ) -> None:
        upload = _upload(client, auth_h, checking_account).json()
        batch_id = upload["id"]
        # Verify lines exist before delete.
        from uuid import UUID

        from tulip_storage.models import StatementLine

        with session_maker() as s:
            count_before = s.query(StatementLine).filter_by(import_batch_id=UUID(batch_id)).count()
            assert count_before > 0

        client.delete(f"/v1/imports/{batch_id}", headers=auth_h)

        with session_maker() as s:
            count_after = s.query(StatementLine).filter_by(import_batch_id=UUID(batch_id)).count()
            assert count_after == 0

    def test_unknown_batch_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        from uuid import uuid4

        r = client.delete(f"/v1/imports/{uuid4()}", headers=auth_h)
        assert_problem(r, code="import_batch.not_found", status=404)

    def test_promoted_lines_block_delete_with_409(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
    ) -> None:
        """If any line was promoted to a ledger transaction, refuse with a
        typed 409 carrying the promoted_count.
        """
        upload = _upload(client, auth_h, checking_account).json()
        batch_id = upload["id"]
        # Apply (promotes all lines into PENDING transactions).
        apply_resp = client.post(
            f"/v1/imports/{batch_id}/apply?no_categorize=true",
            headers=auth_h,
        )
        assert apply_resp.status_code == 200, apply_resp.text
        assert apply_resp.json()["created_count"] > 0

        r = client.delete(f"/v1/imports/{batch_id}", headers=auth_h)
        assert_problem(r, code="import.batch_has_promoted_lines", status=409)
        body = r.json()
        assert body["promoted_count"] >= 1
        assert body["batch_id"] == batch_id

    def test_member_returns_403(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        session_maker,
    ) -> None:
        from sqlalchemy import update

        from tulip_storage.models import User, UserRole

        upload = _upload(client, auth_h, checking_account).json()
        batch_id = upload["id"]
        # Demote admin to member, re-login.
        with session_maker() as s:
            s.execute(update(User).values(role=UserRole.MEMBER))
            s.commit()
        login = client.post(
            "/v1/auth/login",
            json={"email": "admin@example.com", "password": "correct horse battery staple"},
        )
        member_h = {"Authorization": f"Bearer {login.json()['access_token']}"}
        r = client.delete(f"/v1/imports/{batch_id}", headers=member_h)
        assert_problem(r, code="auth.forbidden", status=403)

    def test_audit_row_written(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        checking_account: str,
        session_maker,
    ) -> None:
        upload = _upload(client, auth_h, checking_account).json()
        batch_id = upload["id"]
        client.delete(f"/v1/imports/{batch_id}", headers=auth_h)

        from tulip_storage.models import AuditLog

        with session_maker() as s:
            row = (
                s.query(AuditLog)
                .filter(AuditLog.action == "import_batch.deleted")
                .order_by(AuditLog.occurred_at.desc())
                .first()
            )
            assert row is not None
            assert row.before_snapshot is not None
            assert row.before_snapshot["source_format"] == "ofx"
            assert row.before_snapshot["line_count"] >= 1
            assert row.before_snapshot["promoted_count"] == 0
