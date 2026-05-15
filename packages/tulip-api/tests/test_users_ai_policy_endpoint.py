"""Tests for PUT /v1/users/{me,user_id}/ai-policy — per-user AI policy (#239).

GDPR Art. 18(1)(a)/(d) restriction-of-processing. Members can ratchet up
the strictness of their own AI policy (the household sets the floor).
Admins can set policy on any user in their household. The endpoint only
accepts ``capabilities[<cap>].{policy,profile}`` — provider / cost-cap /
rate-limit remain household-scope per ADR-0005 §Q5.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


def _register_household_admin(client: TestClient) -> dict[str, str]:
    body = {
        "email": "admin@example.com",
        "password": "correct horse battery staple",
        "display_name": "Admin",
        "household_name": "Smith Family",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


@pytest.fixture
def admin_h(client: TestClient) -> dict[str, str]:
    body = _register_household_admin(client)
    r = client.post(
        "/v1/auth/login",
        json={"email": body["email"], "password": body["password"]},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin_user_id(client: TestClient, admin_h: dict[str, str]) -> str:
    """Resolve the admin's own user_id via the export endpoint."""
    r = client.get("/v1/users/me/export", headers=admin_h)
    assert r.status_code == 200
    return r.json()["user"]["id"]


class TestPutOwnAIPolicy:
    def test_member_can_ratchet_up_own_policy(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        r = client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "disabled"}}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ai_policy"]["capabilities"]["nl_query"]["policy"] == "disabled"

    def test_put_reflected_in_ai_status(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """The /v1/ai/status response should reflect the user's stricter policy."""
        client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "disabled"}}},
        )
        r = client.get("/v1/ai/status", headers=admin_h)
        assert r.json()["capabilities"]["nl_query"]["level"] == "disabled"

    def test_clear_resets_to_inherit(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        # Set, then clear by sending an empty body.
        client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "disabled"}}},
        )
        r = client.put("/v1/users/me/ai-policy", headers=admin_h, json={})
        assert r.status_code == 200, r.text
        assert r.json()["ai_policy"] is None

    def test_put_writes_audit_row(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        admin_user_id: str,
        session_maker,
    ) -> None:
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "disabled"}}},
        )
        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "user.ai_policy_set")
            ).scalar_one()
        assert row.entity_type == "user"
        assert str(row.entity_id) == admin_user_id
        assert row.before_snapshot is None or row.before_snapshot == {}
        assert row.after_snapshot is not None
        assert row.after_snapshot["capabilities"]["nl_query"]["policy"] == "disabled"


class TestPutOtherAIPolicy:
    def test_admin_can_set_other_users_policy(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        """Admin sets policy on a sibling member in the same household."""
        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        # Create a sibling member in the admin's household.
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
            json={"capabilities": {"forecast": {"policy": "disabled"}}},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ai_policy"]["capabilities"]["forecast"]["policy"] == "disabled"

    def test_non_admin_cannot_set_others_policy(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        """Member calls PUT /v1/users/{other_id}/ai-policy → 403."""
        from sqlalchemy import select

        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import Household, User, UserRole

        # Seed a member and a target.
        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            target_id = uuid4()
            s.add_all(
                [
                    User(
                        household_id=household.id,
                        id=uuid4(),
                        email="member@example.com",
                        password_hash=hash_password("memberpassword123"),
                        display_name="Member",
                        role=UserRole.MEMBER,
                    ),
                    User(
                        household_id=household.id,
                        id=target_id,
                        email="target@example.com",
                        password_hash=hash_password("targetpassword123"),
                        display_name="Target",
                        role=UserRole.MEMBER,
                    ),
                ]
            )
            s.commit()

        login = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "memberpassword123"},
        )
        member_h = {"Authorization": f"Bearer {login.json()['access_token']}"}

        r = client.put(
            f"/v1/users/{target_id}/ai-policy",
            headers=member_h,
            json={"capabilities": {"forecast": {"policy": "disabled"}}},
        )
        assert_problem(r, code="auth.forbidden", status=403)

    def test_admin_cannot_reach_other_household(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """user_id from another household → 404 (tenant scoping)."""
        # Register a second household; capture its admin's user id.
        client.post(
            "/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "other good password please",
                "display_name": "Other",
                "household_name": "Other Family",
            },
        )
        other_login = client.post(
            "/v1/auth/login",
            json={"email": "other@example.com", "password": "other good password please"},
        )
        other_h = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
        other_id = client.get("/v1/users/me/export", headers=other_h).json()["user"]["id"]

        r = client.put(
            f"/v1/users/{other_id}/ai-policy",
            headers=admin_h,
            json={"capabilities": {"forecast": {"policy": "disabled"}}},
        )
        assert_problem(r, code="user.not_found", status=404)


class TestPutAIPolicyValidation:
    def test_invalid_policy_value_returns_422(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        r = client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"nl_query": {"policy": "nonsense"}}},
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_unknown_capability_rejected(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        r = client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"capabilities": {"made_up": {"policy": "disabled"}}},
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_extra_fields_rejected(
        self,
        client: TestClient,
        admin_h: dict[str, str],
    ) -> None:
        """Provider / cost-cap belong on the household policy, not per-user."""
        r = client.put(
            "/v1/users/me/ai-policy",
            headers=admin_h,
            json={"default_provider": "openai"},
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.put("/v1/users/me/ai-policy", json={})
        assert_problem(r, code="auth.unauthorized", status=401)
