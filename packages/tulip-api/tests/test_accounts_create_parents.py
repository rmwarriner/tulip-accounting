"""Tests for ``POST /v1/accounts`` with ``create_parents=true`` (#46).

When the caller passes a colon-delimited ``code`` like
``assets:current:checking`` and sets ``create_parents=true``, the
endpoint walks the path root → leaf, creating any segment that doesn't
already exist. Existing parents are reused. The whole walk commits in
one transaction, so a mid-path failure rolls back any parents already
created during the same call.

Type inference: the root segment determines the type for every account
in the chain. ``assets`` / ``liabilities`` / ``equity`` / ``income``
/ ``expenses`` (plus singular aliases) map to the API's type enum via
the same ``_TYPE_ALIASES`` table the resolver uses for hierarchical-
path lookups (#197). Unrecognised root segments → 400.
"""

from __future__ import annotations

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


class TestCreateParentsHappyPath:
    def test_creates_full_chain_from_root(self, client: TestClient, auth_h: dict[str, str]) -> None:
        """`assets:current:checking` → asset, asset:current, asset:current:checking."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "assets:current:checking",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Leaf is the returned account.
        assert body["code"] == "assets:current:checking"
        assert body["name"] == "Checking"
        assert body["type"] == "asset"
        # parents_created lists the (newly-created) ancestors root → leaf-parent.
        parents = body.get("parents_created") or []
        assert len(parents) == 2
        assert parents[0]["code"] == "assets"
        assert parents[1]["code"] == "assets:current"
        assert all(p["type"] == "asset" for p in parents)
        # Parent links: assets.parent = null; assets:current.parent = assets;
        # leaf.parent = assets:current.
        assert parents[0]["parent_account_id"] is None
        assert parents[1]["parent_account_id"] == parents[0]["id"]
        assert body["parent_account_id"] == parents[1]["id"]

    def test_reuses_existing_parents(self, client: TestClient, auth_h: dict[str, str]) -> None:
        """Pre-existing parents along the path are reused, not re-created."""
        # Pre-create the root.
        existing = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "assets",
                "type": "asset",
                "currency": "USD",
                "code": "assets",
            },
        ).json()
        # Now create the leaf with create_parents=true; only the new
        # intermediate + leaf get created.
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Savings",
                "type": "asset",
                "currency": "USD",
                "code": "assets:current:savings",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        parents = body.get("parents_created") or []
        # Only the intermediate `assets:current` was created; `assets`
        # was reused, so it's NOT in the parents_created list.
        assert [p["code"] for p in parents] == ["assets:current"]
        # The intermediate's parent links back to the pre-existing root.
        assert parents[0]["parent_account_id"] == existing["id"]

    def test_idempotent_when_full_path_exists(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Re-running the same create-path call after the first returns the existing leaf."""
        first = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "assets:current:checking",
                "create_parents": True,
            },
        )
        assert first.status_code == 201
        first_id = first.json()["id"]

        second = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "assets:current:checking",
                "create_parents": True,
            },
        )
        # Idempotent: the leaf is returned with the original id, no new
        # accounts in parents_created.
        assert second.status_code == 201
        body = second.json()
        assert body["id"] == first_id
        assert (body.get("parents_created") or []) == []

    def test_two_level_path(self, client: TestClient, auth_h: dict[str, str]) -> None:
        """A two-segment path creates the root and the leaf."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Groceries",
                "type": "expense",
                "currency": "USD",
                "code": "expenses:groceries",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        parents = body.get("parents_created") or []
        assert [p["code"] for p in parents] == ["expenses"]
        assert parents[0]["type"] == "expense"


class TestCreateParentsTypeInference:
    @pytest.mark.parametrize(
        ("root_segment", "expected_type"),
        [
            ("assets", "asset"),
            ("asset", "asset"),
            ("liabilities", "liability"),
            ("liability", "liability"),
            ("equity", "equity"),
            ("income", "income"),
            ("expenses", "expense"),
            ("expense", "expense"),
        ],
    )
    def test_root_segment_maps_to_type(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        root_segment: str,
        expected_type: str,
    ) -> None:
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Leaf",
                "type": expected_type,
                "currency": "USD",
                "code": f"{root_segment}:leaf",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Root parent was created with the inferred type.
        parents = body.get("parents_created") or []
        assert parents[0]["type"] == expected_type

    def test_unknown_root_segment_rejected(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """An unrecognised root (e.g. `widgets`) can't infer a type — reject."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Leaf",
                "type": "asset",
                "currency": "USD",
                "code": "widgets:thing:leaf",
                "create_parents": True,
            },
        )
        assert r.status_code == 400
        assert_problem(r, code="account.path_invalid", status=400)

    def test_leaf_type_must_match_inferred(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """The body's `type` must match what the root segment implies."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Leaf",
                "type": "liability",
                "currency": "USD",
                "code": "assets:misnamed:leaf",
                "create_parents": True,
            },
        )
        assert r.status_code == 400
        assert_problem(r, code="account.path_invalid", status=400)


class TestCreateParentsValidation:
    @pytest.mark.parametrize(
        "bad_code",
        [
            "",  # empty
            ":",  # colon-only
            ":assets:leaf",  # leading colon
            "assets:leaf:",  # trailing colon
            "assets::leaf",  # empty middle segment
            "assets:   :leaf",  # whitespace-only segment
            "assets",  # single segment (no parent to create)
        ],
    )
    def test_malformed_path_rejected(
        self, client: TestClient, auth_h: dict[str, str], bad_code: str
    ) -> None:
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Leaf",
                "type": "asset",
                "currency": "USD",
                "code": bad_code,
                "create_parents": True,
            },
        )
        assert r.status_code == 400, r.text
        assert_problem(r, code="account.path_invalid", status=400)

    def test_create_parents_without_any_path_rejected(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """create_parents=true requires a colon-path in *either* ``code`` or ``name``."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Leaf",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 400
        assert_problem(r, code="account.path_invalid", status=400)


class TestCreateParentsNamePath:
    """Name colon-paths are first-class for ``create_parents`` (#416).

    The user-facing convention from PTA tools and Quicken:
    ``Assets:Current Assets:Checking`` is a hierarchy of *names*, not
    codes. ``code`` stays optional throughout — the leaf takes
    ``body.code`` if provided, intermediates have ``code=None``.
    """

    def test_creates_chain_from_name_path_with_no_codes(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets:Current Assets:Checking",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Leaf takes the last segment as its name; no code was provided.
        assert body["name"] == "Checking"
        assert body["code"] is None
        parents = body.get("parents_created") or []
        assert len(parents) == 2
        assert parents[0]["name"] == "Assets"
        assert parents[0]["code"] is None
        assert parents[0]["parent_account_id"] is None
        assert parents[1]["name"] == "Current Assets"
        assert parents[1]["code"] is None
        assert parents[1]["parent_account_id"] == parents[0]["id"]
        assert body["parent_account_id"] == parents[1]["id"]

    def test_name_path_with_leaf_code_only(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """``code`` without a colon is treated as the leaf's short code."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets:Current Assets:Checking",
                "code": "1100",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Checking"
        assert body["code"] == "1100"
        parents = body.get("parents_created") or []
        assert all(p["code"] is None for p in parents)

    def test_name_path_root_segment_drives_type_inference(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """``Liabilities:...`` must be paired with ``type='liability'``."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Liabilities:Credit Cards:Visa",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 400, r.text
        assert_problem(r, code="account.path_invalid", status=400)

    def test_name_path_root_matches_type(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Liabilities:Credit Cards:Visa",
                "type": "liability",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Visa"
        assert body["type"] == "liability"
        parents = body.get("parents_created") or []
        assert [p["name"] for p in parents] == ["Liabilities", "Credit Cards"]
        assert all(p["type"] == "liability" for p in parents)

    def test_both_name_and_code_are_paths_rejected_as_ambiguous(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets:Current Assets:Checking",
                "code": "assets:current:checking",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 400, r.text
        assert_problem(r, code="account.path_invalid", status=400)

    def test_name_path_reuses_existing_by_parent_and_name(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Pre-existing intermediate (matched by parent + name) is reused."""
        # Seed the root.
        existing_root = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets",
                "type": "asset",
                "currency": "USD",
            },
        ).json()
        # Create the leaf with a name-path that traverses the existing root.
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets:Current Assets:Savings",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        parents = body.get("parents_created") or []
        # Only ``Current Assets`` was newly created; ``Assets`` was reused.
        assert [p["name"] for p in parents] == ["Current Assets"]
        assert parents[0]["parent_account_id"] == existing_root["id"]

    def test_name_path_idempotent_when_full_chain_exists(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        first = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets:Current Assets:Checking",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Assets:Current Assets:Checking",
                "type": "asset",
                "currency": "USD",
                "create_parents": True,
            },
        )
        assert second.status_code == 201, second.text
        # Same leaf id; no new parents were created the second time.
        assert second.json()["id"] == first.json()["id"]
        assert (second.json().get("parents_created") or []) == []


class TestCreateParentsBackwardCompat:
    def test_omitting_create_parents_works_as_before(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """The default (create_parents=false) preserves the existing endpoint behavior."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "assets:checking",  # colon allowed but treated as literal
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["code"] == "assets:checking"
        # parents_created not present (or null) in the default response.
        assert body.get("parents_created") in (None, [])

    def test_create_parents_false_explicitly(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Explicit create_parents=false matches the omitted-flag behavior."""
        r = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": "Checking",
                "type": "asset",
                "currency": "USD",
                "code": "assets:checking",
                "create_parents": False,
            },
        )
        assert r.status_code == 201
        assert r.json().get("parents_created") in (None, [])
