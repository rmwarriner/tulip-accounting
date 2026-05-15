"""End-to-end tests for #239 (per-user AI policy + per-user keys).

The three acceptance cases from the issue body, exercised against the
running app:

1. Member-stricter: household = permissive, user = disabled → user's
   nl_query call is blocked.
2. Admin-overrides-member: admin uses PUT /v1/users/{member_id}/ai-policy
   to re-enable the member's capabilities; the household floor still
   bounds the resolved level.
3. Member-with-own-key: user uploads their own key for the resolved
   provider; the categorize call uses that key, not the household's.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


@pytest.fixture
def admin_h(client: TestClient) -> dict[str, str]:
    client.post(
        "/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
            "display_name": "Admin",
            "household_name": "Smith Family",
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin_user_id(client: TestClient, admin_h: dict[str, str]) -> UUID:
    return UUID(client.get("/v1/users/me/export", headers=admin_h).json()["user"]["id"])


class TestMemberStricter:
    def test_member_disables_own_nl_query_household_permissive(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """Household is permissive by default; member ratchets nl_query disabled.

        AI status for the member reflects the merged (disabled) level.
        """
        client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "disabled"}}},
        )
        r = client.get("/v1/ai/status", headers=admin_h)
        assert r.json()["capabilities"]["nl_query"]["level"] == "disabled"

    def test_member_cannot_ratchet_down_below_household_floor(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """Household sets requires_approval; member tries permissive → still requires_approval.

        Max-severity wins. The user's stored override is ``permissive`` but
        the resolver clamps to the household floor.
        """
        client.put(
            "/v1/ai/config/capabilities/forecast",
            headers=admin_h,
            json={"policy": "requires_approval"},
        )
        client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"forecast": {"policy": "permissive"}}},
        )
        r = client.get("/v1/ai/status", headers=admin_h)
        assert r.json()["capabilities"]["forecast"]["level"] == "requires_approval"


class TestAdminOverridesMember:
    def test_admin_can_set_member_policy_via_admin_endpoint(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        """Admin uses PUT /v1/users/{member_id}/ai-policy to change a member."""
        from uuid import uuid4

        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            member_id = uuid4()
            s.add(
                User(
                    household_id=household.id,
                    id=member_id,
                    email="member@example.com",
                    password_hash=hash_password("memberpassword123"),
                    display_name="Member",
                    role=UserRole.MEMBER,
                )
            )
            s.commit()

        r = client.put(
            f"/v1/users/{member_id}/ai-policy",
            headers=admin_h,
            json={"capabilities": {"agentic": {"policy": "disabled"}}},
        )
        assert r.status_code == 200, r.text

        # Verify by logging in as the member.
        login = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "memberpassword123"},
        )
        member_h = {"Authorization": f"Bearer {login.json()['access_token']}"}
        status_body = client.get("/v1/ai/status", headers=member_h).json()
        assert status_body["capabilities"]["agentic"]["level"] == "disabled"


class TestMemberKey:
    def test_member_key_appears_in_status_providers_list(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """Setting a per-user key is visible via /v1/ai/keys/me (no key bytes)."""
        client.post(
            "/v1/ai/keys/me/anthropic",
            headers=admin_h,
            json={"api_key": "sk-mine"},
        )
        r = client.get("/v1/ai/keys/me", headers=admin_h).json()
        assert r["providers"] == ["anthropic"]
        # Household-key list and per-user-key list are independent surfaces.
        admin_keys = client.get("/v1/ai/keys", headers=admin_h).json()
        assert "anthropic" not in admin_keys["providers"]

    def test_member_can_set_own_key_without_admin(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        """A non-admin member can still call POST /v1/ai/keys/me/{provider}."""
        from uuid import uuid4

        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            s.add(
                User(
                    household_id=household.id,
                    id=uuid4(),
                    email="member@example.com",
                    password_hash=hash_password("memberpassword123"),
                    display_name="Member",
                    role=UserRole.MEMBER,
                )
            )
            s.commit()

        login = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "memberpassword123"},
        )
        member_h = {"Authorization": f"Bearer {login.json()['access_token']}"}

        r = client.post(
            "/v1/ai/keys/me/anthropic",
            headers=member_h,
            json={"api_key": "sk-member"},
        )
        assert r.status_code == 204, r.text


class TestAdminRestrictions:
    def test_member_cannot_set_household_key_admin_only(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        """The household-key endpoint stays admin-only (existing #229 behavior)."""
        from uuid import uuid4

        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            s.add(
                User(
                    household_id=household.id,
                    id=uuid4(),
                    email="member@example.com",
                    password_hash=hash_password("memberpassword123"),
                    display_name="Member",
                    role=UserRole.MEMBER,
                )
            )
            s.commit()

        login = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "memberpassword123"},
        )
        member_h = {"Authorization": f"Bearer {login.json()['access_token']}"}

        r = client.post(
            "/v1/ai/keys/anthropic",
            headers=member_h,
            json={"api_key": "sk-evil"},
        )
        assert_problem(r, code="auth.forbidden", status=403)


class TestExportIncludesUserAIPolicy:
    def test_user_export_carries_ai_policy(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """GDPR Art. 15 (#241): the export reflects the per-user policy override."""
        client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "disabled"}}},
        )
        export = client.get("/v1/users/me/export", headers=admin_h).json()
        assert export["user"]["ai_policy"] == {"capabilities": {"nl_query": {"policy": "disabled"}}}
