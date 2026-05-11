"""Tests for /v1/notifications endpoints (P6.3)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from _problem_details import assert_problem


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
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
    )
    return str(r2.json()["access_token"]), str(household_id)


@pytest.fixture
def auth_h(admin_token: tuple[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token[0]}"}


@pytest.fixture
def household_id(admin_token: tuple[str, str]) -> UUID:
    return UUID(admin_token[1])


def _seed_notification(
    client: TestClient,
    auth_h: dict[str, str],
    household_id: UUID,
    *,
    kind: str = "anomaly",
    severity: str = "info",
    dismissed: bool = False,
) -> str:
    """Seed one notification row directly via the test session.

    No endpoint creates notifications today (the scheduler handler is
    the only writer), so tests reach into the repo through the same
    dependency_override the API uses.
    """
    from tulip_api.deps import get_session
    from tulip_storage.repositories import NotificationRepository

    overrides = client.app.dependency_overrides
    session_factory = overrides[get_session]
    with next(session_factory()) as session:
        repo = NotificationRepository(session, household_id)
        row = repo.create(
            kind=kind,
            severity=severity,
            title="Unusual spend",
            body="Spending 1000.00 USD is way above normal.",
            produced_by="daily_insights",
        )
        if dismissed:
            repo.dismiss(row.id)
        session.commit()
        return str(row.id)


class TestList:
    def test_empty_inbox(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.get("/v1/notifications", headers=auth_h)
        assert r.status_code == 200
        assert r.json() == []

    def test_lists_active_only_by_default(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
    ) -> None:
        _seed_notification(client, auth_h, household_id, dismissed=False)
        _seed_notification(client, auth_h, household_id, dismissed=True)
        body = client.get("/v1/notifications", headers=auth_h).json()
        assert len(body) == 1
        assert body[0]["dismissed_at"] is None

    def test_include_dismissed(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
    ) -> None:
        _seed_notification(client, auth_h, household_id, dismissed=False)
        _seed_notification(client, auth_h, household_id, dismissed=True)
        body = client.get("/v1/notifications?include_dismissed=true", headers=auth_h).json()
        assert len(body) == 2


class TestDismiss:
    def test_dismiss_stamps_dismissed_at(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
    ) -> None:
        nid = _seed_notification(client, auth_h, household_id)
        r = client.post(f"/v1/notifications/{nid}/dismiss", headers=auth_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dismissed_at"] is not None

    def test_dismiss_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(f"/v1/notifications/{uuid4()}/dismiss", headers=auth_h)
        assert_problem(r, code="notification.not_found", status=404)

    def test_dismiss_is_idempotent_on_already_dismissed(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
    ) -> None:
        nid = _seed_notification(client, auth_h, household_id, dismissed=True)
        first = client.post(f"/v1/notifications/{nid}/dismiss", headers=auth_h)
        second = client.post(f"/v1/notifications/{nid}/dismiss", headers=auth_h)
        assert second.status_code == 200
        # dismissed_at didn't move on the second call.
        assert first.json()["dismissed_at"] == second.json()["dismissed_at"]

    def test_unauth_returns_401(self, client: TestClient) -> None:
        r = client.post(f"/v1/notifications/{uuid4()}/dismiss")
        assert r.status_code == 401
