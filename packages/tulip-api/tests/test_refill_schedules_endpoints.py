"""Integration tests for /v1/envelopes/{id}/refill-schedule + /v1/scheduled-jobs (P4.3.c)."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem

# ---- Fixtures ---------------------------------------------------------


@pytest.fixture
def admin_token(client: TestClient) -> tuple[str, str]:
    r = client.post(
        "/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
            "display_name": "Admin",
            "household_name": "Smith",
        },
    )
    household_id = r.json()["household_id"]
    r2 = client.post(
        "/v1/auth/login",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )
    return r2.json()["access_token"], household_id


@pytest.fixture
def auth_h(admin_token: tuple[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token[0]}"}


@pytest.fixture
def household_id(admin_token: tuple[str, str]) -> UUID:
    return UUID(admin_token[1])


def _make_envelope_with_rule(
    client: TestClient, auth_h: dict[str, str], *, name: str = "Groceries"
) -> str:
    body = {
        "name": name,
        "currency": "USD",
        "budget_period": "monthly",
        "rollover_policy": "reset",
        "refill_rule": {
            "strategy": "fixed_amount",
            "amount": "250.00",
            "currency": "USD",
        },
    }
    r = client.post("/v1/envelopes", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _make_envelope_no_rule(
    client: TestClient, auth_h: dict[str, str], *, name: str = "NoRule"
) -> str:
    r = client.post(
        "/v1/envelopes",
        headers=auth_h,
        json={
            "name": name,
            "currency": "USD",
            "budget_period": "monthly",
            "rollover_policy": "reset",
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ---- POST /v1/envelopes/{id}/refill-schedule ---------------------------


class TestCreateRefillSchedule:
    def test_creates_schedule_for_envelope_with_rule(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        env_id = _make_envelope_with_rule(client, auth_h)
        r = client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["envelope_id"] == env_id
        assert body["rrule"] == "FREQ=MONTHLY"
        assert body["is_active"] is True
        UUID(body["id"])

    def test_envelope_without_refill_rule_rejected(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        env_id = _make_envelope_no_rule(client, auth_h)
        r = client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        assert_problem(r, code="refill_schedule.envelope_has_no_refill_rule", status=400)

    def test_invalid_rrule_rejected(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        r = client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "NOT_AN_RRULE",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        assert_problem(r, code="refill_schedule.invalid_rrule", status=400)

    def test_unknown_envelope_returns_404(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            f"/v1/envelopes/{uuid4()}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        assert_problem(r, code="envelope.not_found", status=404)

    def test_duplicate_schedule_rejected(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        body = {"rrule": "FREQ=MONTHLY", "start_at": "2026-06-01T12:00:00+00:00"}
        r1 = client.post(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h, json=body)
        assert r1.status_code == 201
        r2 = client.post(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h, json=body)
        assert_problem(r2, code="refill_schedule.already_exists", status=409)


# ---- GET /v1/envelopes/{id}/refill-schedule ----------------------------


class TestGetRefillSchedule:
    def test_returns_schedule(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        r = client.get(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["envelope_id"] == env_id

    def test_returns_404_when_no_schedule(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        r = client.get(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h)
        assert_problem(r, code="refill_schedule.not_found", status=404)

    def test_unknown_envelope_returns_envelope_not_found(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        r = client.get(f"/v1/envelopes/{uuid4()}/refill-schedule", headers=auth_h)
        assert_problem(r, code="envelope.not_found", status=404)


# ---- DELETE /v1/envelopes/{id}/refill-schedule -------------------------


class TestCancelRefillSchedule:
    def test_cancel_marks_inactive(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        r = client.delete(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h)
        assert r.status_code == 204

        # Subsequent GET returns 404 (active=False).
        get_r = client.get(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h)
        assert_problem(get_r, code="refill_schedule.not_found", status=404)

    def test_cancel_unknown_returns_not_found(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        r = client.delete(f"/v1/envelopes/{env_id}/refill-schedule", headers=auth_h)
        assert_problem(r, code="refill_schedule.not_found", status=404)


# ---- GET /v1/scheduled-jobs --------------------------------------------


class TestListScheduledJobs:
    def test_empty_household_returns_empty_list(self, client: TestClient, auth_h: dict[str, str]):
        r = client.get("/v1/scheduled-jobs", headers=auth_h)
        assert r.status_code == 200
        assert r.json() == []

    def test_lists_active_jobs(self, client: TestClient, auth_h: dict[str, str]):
        env_id = _make_envelope_with_rule(client, auth_h)
        client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        r = client.get("/v1/scheduled-jobs", headers=auth_h)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["kind"] == "envelope_refill"
        assert body[0]["rrule"] == "FREQ=MONTHLY"


# ---- POST /v1/scheduled-jobs/run-due -----------------------------------


class TestRunDue:
    def test_run_due_executes_handler(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        app,
        session_maker,
        household_id,
    ):
        # Register the envelope_refill handler on the app.state runner so
        # run-due actually executes the handler. Tests for run-due are
        # the one place where the runner needs handler registration.
        from tulip_storage.runner.handlers import make_envelope_refill_handler

        app.state.runner.register_handler(
            "envelope_refill", make_envelope_refill_handler(session_maker)
        )

        env_id = _make_envelope_with_rule(client, auth_h)
        # Schedule fires immediately (start_at = today).
        client.post(
            f"/v1/envelopes/{env_id}/refill-schedule",
            headers=auth_h,
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": date.today().isoformat() + "T00:00:00+00:00",
            },
        )

        r = client.post("/v1/scheduled-jobs/run-due", headers=auth_h)
        assert r.status_code == 200, r.text
        assert r.json()["fired"] == 1

        # Envelope balance now 250.
        bal_r = client.get(f"/v1/envelopes/{env_id}/balance", headers=auth_h)
        assert bal_r.status_code == 200
        from decimal import Decimal

        assert Decimal(bal_r.json()["balance"]) == Decimal("250.00")

    def test_run_due_with_nothing_due_returns_zero(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        r = client.post("/v1/scheduled-jobs/run-due", headers=auth_h)
        assert r.status_code == 200
        assert r.json()["fired"] == 0


# ---- Unauthenticated ---------------------------------------------------


class TestRefillScheduleUnauthenticated:
    def test_create_without_token_returns_401(self, client: TestClient):
        r = client.post(
            f"/v1/envelopes/{uuid4()}/refill-schedule",
            json={
                "rrule": "FREQ=MONTHLY",
                "start_at": "2026-06-01T12:00:00+00:00",
            },
        )
        assert r.status_code == 401

    def test_list_without_token_returns_401(self, client: TestClient):
        r = client.get("/v1/scheduled-jobs")
        assert r.status_code == 401
