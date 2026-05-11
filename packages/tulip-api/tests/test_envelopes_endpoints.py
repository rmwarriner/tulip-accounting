"""Integration tests for /v1/envelopes (P4.1.b).

Covers CRUD + balance + refill, including the visibility/role rules and
the audit-log shape.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem

# ---- Fixtures ---------------------------------------------------------


def _register_admin(client: TestClient, email: str, household_name: str) -> tuple[str, str]:
    r = client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple",
            "display_name": "Admin",
            "household_name": household_name,
        },
    )
    household_id = r.json()["household_id"]
    r2 = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    return r2.json()["access_token"], household_id


@pytest.fixture
def admin_token(client: TestClient) -> tuple[str, str]:
    return _register_admin(client, "admin@example.com", "Smith")


@pytest.fixture
def auth_h(admin_token: tuple[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token[0]}"}


@pytest.fixture
def household_id(admin_token: tuple[str, str]) -> UUID:
    return UUID(admin_token[1])


# ---- CRUD happy path --------------------------------------------------


class TestCreateEnvelope:
    def test_minimal_envelope_succeeds(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Groceries"
        assert body["currency"] == "USD"
        assert body["budget_period"] == "monthly"
        assert body["rollover_policy"] == "reset"
        assert body["budget_amount"] is None
        assert body["refill_rule"] is None
        assert body["is_active"] is True
        assert body["visibility"] == "shared"
        UUID(body["id"])  # parses

    def test_full_envelope_with_refill_rule(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Rent",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "accumulate",
                "budget_amount": "2500.00",
                "refill_rule": {
                    "strategy": "fixed_amount",
                    "amount": "2500.00",
                    "currency": "USD",
                },
                "visibility": "private",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["budget_amount"] == "2500.00"
        assert body["refill_rule"]["strategy"] == "fixed_amount"
        assert body["visibility"] == "private"

    def test_invalid_strategy_rejected(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Bad",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
                "refill_rule": {"strategy": "magic"},
            },
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_negative_budget_amount_rejected(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Bad",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
                "budget_amount": "-1.00",
            },
        )
        assert_problem(r, code="validation.failed", status=422)

    # Note: currency validation against the ISO whitelist isn't enforced at
    # envelope creation in v1 — same as account creation. The whitelist
    # check happens lazily when Money is constructed (e.g. on first balance
    # read or refill). A future PR can tighten this.


class TestListAndGetEnvelope:
    def test_list_empty_for_new_household(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/envelopes", headers=auth_h)
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_created(self, client: TestClient, auth_h: dict[str, str]):
        client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        )
        r = client.get("/v1/envelopes", headers=auth_h)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["name"] == "Groceries"

    def test_get_by_id(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.get(f"/v1/envelopes/{created['id']}", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get(f"/v1/envelopes/{uuid4()}", headers=auth_h)
        assert_problem(r, code="envelope.not_found", status=404)


class TestUpdateEnvelope:
    def test_patch_name_and_budget(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.patch(
            f"/v1/envelopes/{created['id']}",
            headers=auth_h,
            json={"name": "Food", "budget_amount": "500.00"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Food"
        assert body["budget_amount"] == "500.00"
        assert body["rollover_policy"] == "reset"  # untouched

    def test_patch_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.patch(
            f"/v1/envelopes/{uuid4()}",
            headers=auth_h,
            json={"name": "X"},
        )
        assert_problem(r, code="envelope.not_found", status=404)


class TestDeactivateEnvelope:
    def test_delete_marks_inactive(self, client: TestClient, auth_h: dict[str, str]):
        created = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.delete(f"/v1/envelopes/{created['id']}", headers=auth_h)
        assert r.status_code == 204
        # No longer in list_active.
        listed = client.get("/v1/envelopes", headers=auth_h).json()
        assert listed == []

    def test_delete_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.delete(f"/v1/envelopes/{uuid4()}", headers=auth_h)
        assert_problem(r, code="envelope.not_found", status=404)


# ---- Balance --------------------------------------------------------


class TestEnvelopeBalance:
    def test_balance_zero_for_new_envelope(self, client: TestClient, auth_h: dict[str, str]):
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.get(f"/v1/envelopes/{env['id']}/balance", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pool_id"] == env["id"]
        assert body["currency"] == "USD"
        assert Decimal(body["balance"]) == Decimal("0.00")
        # Endpoint resolves "today" in UTC (#141); local time may differ
        # by a date around the day boundary in negative-offset zones.
        from datetime import UTC, datetime

        assert body["as_of"] == datetime.now(UTC).date().isoformat()

    def test_balance_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get(f"/v1/envelopes/{uuid4()}/balance", headers=auth_h)
        assert_problem(r, code="envelope.not_found", status=404)


# ---- Refill ---------------------------------------------------------


class TestRefillEnvelope:
    def test_refill_moves_unallocated_to_envelope(self, client: TestClient, auth_h: dict[str, str]):
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.post(
            f"/v1/envelopes/{env['id']}/refill",
            headers=auth_h,
            json={
                "amount": "250.00",
                "date": date.today().isoformat(),
                "description": "Monthly grocery refill",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["pool_id"] == env["id"]
        assert Decimal(body["balance"]) == Decimal("250.00")

    def test_refill_unknown_envelope_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            f"/v1/envelopes/{uuid4()}/refill",
            headers=auth_h,
            json={
                "amount": "100",
                "date": date.today().isoformat(),
                "description": "X",
            },
        )
        assert_problem(r, code="envelope.not_found", status=404)

    def test_refill_negative_amount_rejected_at_schema(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.post(
            f"/v1/envelopes/{env['id']}/refill",
            headers=auth_h,
            json={
                "amount": "-100",
                "date": date.today().isoformat(),
                "description": "X",
            },
        )
        assert_problem(r, code="validation.failed", status=422)

    def test_unallocated_can_go_negative(self, client: TestClient, auth_h: dict[str, str]):
        # No budget-inflow declared; refill anyway. v1 is permissive on
        # over-allocation — Unallocated just goes negative.
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        r = client.post(
            f"/v1/envelopes/{env['id']}/refill",
            headers=auth_h,
            json={
                "amount": "100",
                "date": date.today().isoformat(),
                "description": "Over-allocate",
            },
        )
        assert r.status_code == 201, r.text


# ---- Visibility / role ----------------------------------------------


class TestVisibilityAndRoles:
    def test_member_cannot_edit_others_private_envelope(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        session_maker: sessionmaker[Session],
        household_id: UUID,
    ):
        # Admin creates a private envelope.
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Admin Private",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
                "visibility": "private",
            },
        ).json()
        # Add a member user directly to the household and obtain their token.
        from tulip_api.auth.passwords import hash_password
        from tulip_storage.models import User, UserRole

        with session_maker() as s:
            member = User(
                household_id=household_id,
                id=uuid4(),
                email="member@example.com",
                password_hash=hash_password("correct horse battery staple"),
                display_name="Member",
                role=UserRole.MEMBER,
            )
            s.add(member)
            s.commit()
        member_token = client.post(
            "/v1/auth/login",
            json={"email": "member@example.com", "password": "correct horse battery staple"},
        ).json()["access_token"]
        member_h = {"Authorization": f"Bearer {member_token}"}

        # Member cannot see admin's private envelope (404, not 403 — don't leak).
        r = client.get(f"/v1/envelopes/{env['id']}", headers=member_h)
        assert_problem(r, code="envelope.not_found", status=404)

        # Member's PATCH attempt also surfaces as 404.
        r = client.patch(
            f"/v1/envelopes/{env['id']}",
            headers=member_h,
            json={"name": "Hijacked"},
        )
        assert_problem(r, code="envelope.not_found", status=404)


class TestUnauthenticated:
    def test_list_without_token_returns_401(self, client: TestClient):
        r = client.get("/v1/envelopes")
        assert r.status_code == 401


# ---- Audit log ------------------------------------------------------


class TestAuditLog:
    def test_create_writes_audit_row(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        session_maker: sessionmaker[Session],
        household_id: UUID,
    ):
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()

        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(
                    AuditLog.household_id == household_id,
                    AuditLog.entity_type == "envelope",
                    AuditLog.entity_id == UUID(env["id"]),
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.action == "create"
            assert row.after_snapshot is not None
            assert row.after_snapshot["name"] == "Groceries"

    def test_refill_writes_shadow_audit_row(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        session_maker: sessionmaker[Session],
        household_id: UUID,
    ):
        env = client.post(
            "/v1/envelopes",
            headers=auth_h,
            json={
                "name": "Groceries",
                "currency": "USD",
                "budget_period": "monthly",
                "rollover_policy": "reset",
            },
        ).json()
        client.post(
            f"/v1/envelopes/{env['id']}/refill",
            headers=auth_h,
            json={
                "amount": "100",
                "date": date.today().isoformat(),
                "description": "Refill",
            },
        )

        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        with session_maker() as s:
            rows = list(
                s.execute(
                    select(AuditLog).where(
                        AuditLog.household_id == household_id,
                        AuditLog.entity_type == "shadow_transaction",
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) >= 1
            after = rows[0].after_snapshot or {}
            assert after.get("reason") == "refill"
            assert after.get("status") == "posted"
