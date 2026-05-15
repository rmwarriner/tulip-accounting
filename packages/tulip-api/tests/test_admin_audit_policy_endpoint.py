"""Tests for /v1/admin/audit-policy + /v1/admin/audit-prune (#245).

The endpoints are admin-only. PUT writes a ``household.audit_policy_set``
audit row carrying before/after of the stored JSON. POST runs the prune
synchronously for the caller's household.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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


class TestGetAuditPolicy:
    def test_fresh_household_returns_code_defaults(
        self, client: TestClient, admin_h: dict[str, str]
    ) -> None:
        r = client.get("/v1/admin/audit-policy", headers=admin_h)
        assert r.status_code == 200, r.text
        body = r.json()
        # Defaults from _TIER_DEFAULTS.
        assert body["ledger_days"] == 2555
        assert body["auth_days"] == 90
        assert body["ai_days"] == 30
        assert body["admin_days"] == 365
        assert body["default_days"] == 90

    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.get("/v1/admin/audit-policy")
        assert_problem(r, code="auth.unauthorized", status=401)

    def test_member_returns_403(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
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

        r = client.get("/v1/admin/audit-policy", headers=member_h)
        assert_problem(r, code="auth.forbidden", status=403)


class TestPutAuditPolicy:
    def test_set_ledger_days_overrides_default(
        self, client: TestClient, admin_h: dict[str, str]
    ) -> None:
        r = client.put(
            "/v1/admin/audit-policy",
            headers=admin_h,
            json={"ledger_days": 1825},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ledger_days"] == 1825
        # Other tiers stay at their defaults.
        assert body["auth_days"] == 90
        assert body["ai_days"] == 30

    def test_zero_or_negative_rejected_at_schema(
        self, client: TestClient, admin_h: dict[str, str]
    ) -> None:
        r = client.put(
            "/v1/admin/audit-policy",
            headers=admin_h,
            json={"ledger_days": 0},
        )
        assert_problem(r, code="validation.failed", status=422)

        r = client.put(
            "/v1/admin/audit-policy",
            headers=admin_h,
            json={"auth_days": -1},
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_extra_field_rejected(self, client: TestClient, admin_h: dict[str, str]) -> None:
        r = client.put(
            "/v1/admin/audit-policy",
            headers=admin_h,
            json={"ledger_days": 1825, "made_up_tier": 90},
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_put_writes_consent_audit_row(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        from tulip_storage.models import AuditLog

        client.put(
            "/v1/admin/audit-policy",
            headers=admin_h,
            json={"ledger_days": 1825},
        )

        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "household.audit_policy_set"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id is not None
        assert row.before_snapshot == {}
        assert row.after_snapshot == {"ledger_days": 1825}

    def test_member_returns_403(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
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

        r = client.put("/v1/admin/audit-policy", headers=member_h, json={"ledger_days": 1825})
        assert_problem(r, code="auth.forbidden", status=403)


class TestPostAuditPrune:
    def test_prune_with_no_old_rows_returns_zero(
        self, client: TestClient, admin_h: dict[str, str]
    ) -> None:
        r = client.post("/v1/admin/audit-prune", headers=admin_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_deleted"] == 0

    def test_prune_deletes_old_rows_and_writes_summary(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
        from tulip_storage.models import AuditLog, Household

        # Seed an old audit row (200 days for an auth-tier action, past the 90d default).
        with session_maker() as s:
            household = s.execute(select(Household)).scalar_one()
            s.add(
                AuditLog(
                    id=uuid4(),
                    household_id=household.id,
                    occurred_at=datetime.now(tz=UTC) - timedelta(days=200),
                    actor_kind="user",
                    action="login_failed",
                    entity_type="user",
                    entity_id=household.id,
                )
            )
            s.commit()

        r = client.post("/v1/admin/audit-prune", headers=admin_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted_per_tier"]["auth_days"] >= 1
        assert body["total_deleted"] >= 1

        with session_maker() as s:
            survivors = list(
                s.execute(select(AuditLog).where(AuditLog.action == "login_failed")).scalars().all()
            )
            summary = list(
                s.execute(select(AuditLog).where(AuditLog.action == "audit.pruned")).scalars().all()
            )
        assert survivors == []
        assert len(summary) == 1

    def test_member_returns_403(
        self,
        client: TestClient,
        admin_h: dict[str, str],
        session_maker,
    ) -> None:
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

        r = client.post("/v1/admin/audit-prune", headers=member_h)
        assert_problem(r, code="auth.forbidden", status=403)
