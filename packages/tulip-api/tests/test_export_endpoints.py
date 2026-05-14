"""Tests for the GDPR Art. 15 data-subject-access export (#241).

Covers ``GET /v1/users/me/export`` (self) and
``GET /v1/users/{user_id}/export`` (admin-only, household-scoped).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem
from tulip_storage.models import AIInvocation, AuditLog, Household, MfaRecoveryCode, User, UserRole

REG_PASSWORD = "correct horse battery staple"


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    body = {
        "email": "alice@example.com",
        "password": REG_PASSWORD,
        "display_name": "Alice",
        "household_name": "Smith",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


def _access_token(client: TestClient, email: str) -> str:
    r = client.post("/v1/auth/login", json={"email": email, "password": REG_PASSWORD})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


def _seed_member(session_maker: sessionmaker[Session], household_id, *, email: str) -> User:
    """Seed a non-admin member who can log in (hashed REG_PASSWORD)."""
    from tulip_api.auth.passwords import hash_password

    with session_maker() as s:
        u = User(
            household_id=household_id,
            id=uuid4(),
            email=email,
            password_hash=hash_password(REG_PASSWORD),
            display_name=email.split("@")[0].title(),
            role=UserRole.MEMBER,
        )
        s.add(u)
        s.commit()
        return u


class TestExportOwnData:
    def test_envelope_shape_and_masked_password(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ) -> None:
        with session_maker() as s:
            user = s.execute(select(User)).scalar_one()
            s.add(
                AIInvocation(
                    household_id=user.household_id,
                    id=uuid4(),
                    actor_user_id=user.id,
                    capability="nl_query",
                    policy_resolved="permissive",
                    profile="default",
                    outcome="success",
                    prompt_hash=b"\x00" * 32,
                )
            )
            s.add(
                MfaRecoveryCode(
                    id=uuid4(),
                    household_id=user.household_id,
                    user_id=user.id,
                    code_hash="hashed-code",
                )
            )
            s.commit()

        access = _access_token(client, registered["email"])
        r = client.get("/v1/users/me/export", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["user"]["email"] == registered["email"]
        assert body["user"]["password_hash"] == "***"
        # The login that minted `access` created a session row.
        assert len(body["sessions"]) >= 1
        assert len(body["ai_invocations"]) == 1
        assert body["recovery_codes"]["total"] == 1
        assert body["recovery_codes"]["remaining"] == 1
        # Every envelope key is present.
        assert set(body) >= {
            "generated_at",
            "user",
            "sessions",
            "audit_log_where_actor",
            "ai_invocations",
            "proposals_created",
            "proposals_decided",
            "attachments_uploaded",
            "recovery_codes",
            "transactions_created",
        }

    def test_export_is_audited(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ) -> None:
        access = _access_token(client, registered["email"])
        client.get(
            "/v1/users/me/export", headers={"Authorization": f"Bearer {access}"}
        ).raise_for_status()
        with session_maker() as s:
            actions = [row.action for row in s.execute(select(AuditLog)).scalars()]
        assert "user.data_exported" in actions

    def test_requires_auth(self, client: TestClient) -> None:
        r = client.get("/v1/users/me/export")
        assert r.status_code == 401


class TestExportMemberData:
    def test_admin_can_export_a_member(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ) -> None:
        with session_maker() as s:
            household_id = s.execute(select(Household)).scalar_one().id
        member = _seed_member(session_maker, household_id, email="bob@example.com")

        access = _access_token(client, registered["email"])  # Alice is admin
        r = client.get(
            f"/v1/users/{member.id}/export",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user"]["email"] == "bob@example.com"
        assert body["user"]["password_hash"] == "***"

    def test_member_cannot_export_another_user(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ) -> None:
        with session_maker() as s:
            admin_id = s.execute(select(User)).scalar_one().id
            household_id = s.execute(select(Household)).scalar_one().id
        _seed_member(session_maker, household_id, email="bob@example.com")

        member_access = _access_token(client, "bob@example.com")
        r = client.get(
            f"/v1/users/{admin_id}/export",
            headers={"Authorization": f"Bearer {member_access}"},
        )
        assert_problem(r, code="auth.forbidden", status=403)

    def test_unknown_user_returns_404(self, client: TestClient, registered: dict[str, str]) -> None:
        access = _access_token(client, registered["email"])
        r = client.get(
            f"/v1/users/{uuid4()}/export",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert_problem(r, code="user.not_found", status=404)
