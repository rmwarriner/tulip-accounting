"""Tests for /v1/ai/proposals endpoints (P6.4)."""

from __future__ import annotations

from decimal import Decimal
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


def _make_envelope(
    client: TestClient, auth_h: dict[str, str], *, budget_amount: str | None = "100.00"
) -> str:
    body: dict[str, object] = {
        "name": "Groceries",
        "currency": "USD",
        "budget_period": "monthly",
        "rollover_policy": "reset",
    }
    if budget_amount is not None:
        body["budget_amount"] = budget_amount
    r = client.post("/v1/envelopes", headers=auth_h, json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _propose_envelope_budget_update(
    client: TestClient,
    auth_h: dict[str, str],
    *,
    envelope_id: str,
    new_amount: str,
) -> dict[str, object]:
    r = client.post(
        "/v1/ai/proposals",
        headers=auth_h,
        json={
            "kind": "envelope_budget_update",
            "title": f"Bump envelope {envelope_id[:8]} to {new_amount}",
            "payload": {"envelope_id": envelope_id, "new_budget_amount": new_amount},
            "rationale": "Spending up 20% over the last 60 days.",
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _seed_ai_proposal(
    client: TestClient,
    household_id: UUID,
    *,
    envelope_id: str,
    new_amount: str = "175.00",
) -> tuple[str, str]:
    """Seed an AI-created proposal + its ai_invocations row via the repo.

    The HTTP create endpoint is user-only (#218); AI proposals are
    written by the suggest endpoint, which needs a live adapter. For
    audit-chain tests we seed the same rows that path produces. The
    composite FK (#231) requires ai_invocation_id to reference a real
    ai_invocations row in the same household. Returns
    ``(proposal_id, ai_invocation_id)``.
    """
    from tulip_api.deps import get_session
    from tulip_storage.models import AIInvocation, ProposalCreatorKind
    from tulip_storage.repositories import PendingProposalRepository

    session_factory = client.app.dependency_overrides[get_session]
    with next(session_factory()) as s:
        invocation_id = uuid4()
        s.add(
            AIInvocation(
                household_id=household_id,
                id=invocation_id,
                capability="agentic",
                policy_resolved="permissive",
                profile="default",
                outcome="success",
                prompt_hash=b"\x00" * 32,
            )
        )
        s.flush()
        row = PendingProposalRepository(s, household_id).create(
            kind="envelope_budget_update",
            title="AI-suggested",
            payload={"envelope_id": envelope_id, "new_budget_amount": new_amount},
            rationale="",
            created_by_kind=ProposalCreatorKind.AI_AGENT.value,
            created_by_user_id=None,
            ai_invocation_id=invocation_id,
        )
        s.commit()
        return str(row.id), str(invocation_id)


class TestCreateAndList:
    def test_create_user_proposal_sets_creator_kind_user(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        body = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="250.00"
        )
        assert body["created_by_kind"] == "user"
        assert body["status"] == "pending"
        assert body["ai_invocation_id"] is None

    def test_create_rejects_ai_invocation_id_field(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """#218: clients cannot spoof `created_by_kind=ai_agent` by supplying
        `ai_invocation_id` in the create body. The field is now schema-rejected.
        """
        env_id = _make_envelope(client, auth_h)
        r = client.post(
            "/v1/ai/proposals",
            headers=auth_h,
            json={
                "kind": "envelope_budget_update",
                "title": "AI-suggested bump",
                "payload": {"envelope_id": env_id, "new_budget_amount": "200.00"},
                "ai_invocation_id": str(uuid4()),
            },
        )
        # extra="forbid" on ProposalCreate → 422 validation.failed.
        assert_problem(r, code="validation.failed", status=422)

    def test_create_always_sets_creator_kind_user(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """The HTTP create body is user-only; the AI flow writes via the repo directly."""
        env_id = _make_envelope(client, auth_h)
        body = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        assert body["created_by_kind"] == "user"
        assert body["ai_invocation_id"] is None

    def test_list_filters_to_pending_by_default(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        p1 = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        p2 = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="300.00"
        )
        client.post(f"/v1/ai/proposals/{p1['id']}/reject", headers=auth_h)
        body = client.get("/v1/ai/proposals", headers=auth_h).json()
        ids = [r["id"] for r in body]
        assert p2["id"] in ids
        assert p1["id"] not in ids

    def test_list_status_empty_returns_all(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        _propose_envelope_budget_update(client, auth_h, envelope_id=env_id, new_amount="200.00")
        body = client.get("/v1/ai/proposals?status=", headers=auth_h).json()
        assert len(body) == 1

    def test_kinds_endpoint_lists_supported(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.get("/v1/ai/proposals/kinds", headers=auth_h)
        assert r.status_code == 200
        assert "envelope_budget_update" in r.json()

    def test_create_writes_audit_row(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """#222: proposal.create lands its own audit_log row."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        env_id = _make_envelope(client, auth_h)
        _propose_envelope_budget_update(client, auth_h, envelope_id=env_id, new_amount="250.00")
        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "proposal.create"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].entity_type == "proposal"

    def test_reject_writes_audit_row(
        self, client: TestClient, auth_h: dict[str, str], session_maker
    ) -> None:
        """#222: proposal.reject lands an audit_log row."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        env_id = _make_envelope(client, auth_h)
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="250.00"
        )
        r = client.post(f"/v1/ai/proposals/{proposal['id']}/reject", headers=auth_h)
        assert r.status_code == 200
        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "proposal.reject"))
                .scalars()
                .all()
            )
        assert len(rows) == 1


class TestApprove:
    def test_approve_envelope_budget_update_changes_envelope(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
    ) -> None:
        env_id = _make_envelope(client, auth_h, budget_amount="100.00")
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="250.00"
        )
        r = client.post(
            f"/v1/ai/proposals/{proposal['id']}/approve",
            headers=auth_h,
            json={"note": "Looks right."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["decision_note"] == "Looks right."

        # Envelope's budget_amount actually updated.
        env_body = client.get(f"/v1/envelopes/{env_id}", headers=auth_h).json()
        assert Decimal(env_body["budget_amount"]) == Decimal("250.00")

    def test_approve_ai_proposal_writes_actor_kind_ai_agent_audit_row(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker,
    ) -> None:
        """The locked rule from ARCHITECTURE.md §6.2 / THREAT_MODEL §5.3.

        The executor's audit row carries actor_kind=ai_agent and links
        back to both the originating proposal and — per #240 — its
        ai_invocation_id, for chain integrity.
        """
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        env_id = _make_envelope(client, auth_h, budget_amount="100.00")
        proposal_id, invocation_id = _seed_ai_proposal(client, household_id, envelope_id=env_id)

        client.post(f"/v1/ai/proposals/{proposal_id}/approve", headers=auth_h).raise_for_status()

        with session_maker() as session:
            rows = list(
                session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "envelope",
                        AuditLog.actor_kind == "ai_agent",
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].metadata_["proposal_id"] == proposal_id
        assert rows[0].metadata_["ai_invocation_id"] == invocation_id

    def test_approve_audit_row_carries_ai_invocation_id(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker,
    ) -> None:
        """#240: the proposal.approve audit row links to the ai_invocation_id."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        env_id = _make_envelope(client, auth_h, budget_amount="100.00")
        proposal_id, invocation_id = _seed_ai_proposal(client, household_id, envelope_id=env_id)
        client.post(f"/v1/ai/proposals/{proposal_id}/approve", headers=auth_h).raise_for_status()
        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "proposal.approve")
            ).scalar_one()
        assert row.metadata_["ai_invocation_id"] == invocation_id

    def test_approve_user_proposal_writes_actor_kind_user(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ) -> None:
        env_id = _make_envelope(client, auth_h, budget_amount="100.00")
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="150.00"
        )
        client.post(f"/v1/ai/proposals/{proposal['id']}/approve", headers=auth_h).raise_for_status()

        from tulip_api.deps import get_session
        from tulip_storage.models import AuditLog

        overrides = client.app.dependency_overrides
        session_factory = overrides[get_session]
        with next(session_factory()) as session:
            from sqlalchemy import select

            rows = (
                session.execute(select(AuditLog).where(AuditLog.entity_type == "envelope"))
                .scalars()
                .all()
            )
            # The envelope creation itself wrote an audit row; the
            # update from the approve flow is the second one.
            kinds = {r.actor_kind for r in rows}
            assert "user" in kinds

    def test_approve_unknown_proposal_returns_404(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.post(f"/v1/ai/proposals/{uuid4()}/approve", headers=auth_h)
        assert_problem(r, code="proposal.not_found", status=404)

    def test_approve_already_decided_returns_409(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        client.post(f"/v1/ai/proposals/{proposal['id']}/approve", headers=auth_h).raise_for_status()
        again = client.post(f"/v1/ai/proposals/{proposal['id']}/approve", headers=auth_h)
        assert_problem(again, code="proposal.already_decided", status=409)

    def test_approve_unsupported_kind_400(self, client: TestClient, auth_h: dict[str, str]) -> None:
        # Create a proposal for a kind no executor knows; the create
        # endpoint accepts any string, so the failure surfaces on approve.
        r = client.post(
            "/v1/ai/proposals",
            headers=auth_h,
            json={
                "kind": "transfer_pools",
                "title": "Move funds",
                "payload": {},
            },
        )
        proposal_id = r.json()["id"]
        again = client.post(f"/v1/ai/proposals/{proposal_id}/approve", headers=auth_h)
        assert_problem(again, code="proposal.unsupported_kind", status=400)

    def test_approve_invalid_payload_400(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(
            "/v1/ai/proposals",
            headers=auth_h,
            json={
                "kind": "envelope_budget_update",
                "title": "Garbage",
                "payload": {"oops": "no envelope_id"},
            },
        )
        proposal_id = r.json()["id"]
        again = client.post(f"/v1/ai/proposals/{proposal_id}/approve", headers=auth_h)
        assert_problem(again, code="proposal.payload_invalid", status=400)


class TestReject:
    def test_reject_stamps_status(self, client: TestClient, auth_h: dict[str, str]) -> None:
        env_id = _make_envelope(client, auth_h)
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        r = client.post(
            f"/v1/ai/proposals/{proposal['id']}/reject",
            headers=auth_h,
            json={"note": "Not the right approach."},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        assert r.json()["decision_note"] == "Not the right approach."

    def test_reject_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(f"/v1/ai/proposals/{uuid4()}/reject", headers=auth_h)
        assert_problem(r, code="proposal.not_found", status=404)

    def test_reject_idempotent_on_already_rejected(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        first = client.post(f"/v1/ai/proposals/{proposal['id']}/reject", headers=auth_h)
        second = client.post(f"/v1/ai/proposals/{proposal['id']}/reject", headers=auth_h)
        assert second.status_code == 200
        # decided_at didn't move on the second call. SQLite +
        # SQLAlchemy DateTime(timezone=True) round-trips lose the tz on
        # read so the strings differ in trailing 'Z'; compare the
        # underlying instant by stripping it.
        assert first.json()["decided_at"].rstrip("Z") == second.json()["decided_at"].rstrip("Z")

    def test_reject_audit_row_carries_ai_invocation_id(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker,
    ) -> None:
        """#240: the proposal.reject audit row links to the ai_invocation_id."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        env_id = _make_envelope(client, auth_h)
        proposal_id, invocation_id = _seed_ai_proposal(client, household_id, envelope_id=env_id)
        client.post(f"/v1/ai/proposals/{proposal_id}/reject", headers=auth_h).raise_for_status()
        with session_maker() as s:
            row = s.execute(
                select(AuditLog).where(AuditLog.action == "proposal.reject")
            ).scalar_one()
        assert row.metadata_["ai_invocation_id"] == invocation_id


class TestDelete:
    def test_delete_rejected_proposal_hard_deletes(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        client.post(f"/v1/ai/proposals/{proposal['id']}/reject", headers=auth_h).raise_for_status()
        r = client.delete(f"/v1/ai/proposals/{proposal['id']}", headers=auth_h)
        assert r.status_code == 204, r.text
        # Gone — even an unfiltered list doesn't show it.
        body = client.get("/v1/ai/proposals?status=", headers=auth_h).json()
        assert proposal["id"] not in [p["id"] for p in body]

    def test_delete_writes_audit_row_with_ai_invocation_id(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker,
    ) -> None:
        """#240: proposal.delete lands an audit row carrying the ai_invocation_id."""
        from sqlalchemy import select

        from tulip_storage.models import AuditLog

        env_id = _make_envelope(client, auth_h)
        proposal_id, invocation_id = _seed_ai_proposal(client, household_id, envelope_id=env_id)
        client.post(f"/v1/ai/proposals/{proposal_id}/reject", headers=auth_h).raise_for_status()
        client.delete(f"/v1/ai/proposals/{proposal_id}", headers=auth_h).raise_for_status()
        with session_maker() as s:
            rows = list(
                s.execute(select(AuditLog).where(AuditLog.action == "proposal.delete"))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].entity_type == "proposal"
        assert rows[0].metadata_["ai_invocation_id"] == invocation_id

    def test_delete_approved_returns_409(self, client: TestClient, auth_h: dict[str, str]) -> None:
        env_id = _make_envelope(client, auth_h, budget_amount="100.00")
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="150.00"
        )
        client.post(f"/v1/ai/proposals/{proposal['id']}/approve", headers=auth_h).raise_for_status()
        r = client.delete(f"/v1/ai/proposals/{proposal['id']}", headers=auth_h)
        assert_problem(r, code="proposal.not_deletable", status=409)

    def test_delete_pending_returns_409(self, client: TestClient, auth_h: dict[str, str]) -> None:
        env_id = _make_envelope(client, auth_h)
        proposal = _propose_envelope_budget_update(
            client, auth_h, envelope_id=env_id, new_amount="200.00"
        )
        r = client.delete(f"/v1/ai/proposals/{proposal['id']}", headers=auth_h)
        assert_problem(r, code="proposal.not_deletable", status=409)

    def test_delete_unknown_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.delete(f"/v1/ai/proposals/{uuid4()}", headers=auth_h)
        assert_problem(r, code="proposal.not_found", status=404)

    def test_delete_requires_auth(self, client: TestClient) -> None:
        r = client.delete(f"/v1/ai/proposals/{uuid4()}")
        assert r.status_code == 401


class TestAuth:
    def test_create_requires_auth(self, client: TestClient) -> None:
        r = client.post("/v1/ai/proposals", json={"kind": "x", "title": "y", "payload": {}})
        assert r.status_code == 401

    def test_approve_requires_auth(self, client: TestClient) -> None:
        r = client.post(f"/v1/ai/proposals/{uuid4()}/approve")
        assert r.status_code == 401


class TestSuggestBudget:
    """``POST /v1/ai/proposals/suggest/budget`` (P6.4.b).

    The endpoint instantiates ``LitellmAdapter`` inline (no DI hook), so
    happy-path adapter behaviour is covered by the capability unit tests
    in ``tulip-ai/tests/test_proposals.py``. These tests cover the HTTP
    contract: missing api key surfaces a structured error, missing envelope
    is a 404, and unauthenticated callers are rejected.
    """

    def test_requires_auth(self, client: TestClient) -> None:
        r = client.post(
            "/v1/ai/proposals/suggest/budget",
            json={"envelope_id": str(uuid4())},
        )
        assert r.status_code == 401

    def test_unknown_envelope_returns_404(self, client: TestClient, auth_h: dict[str, str]) -> None:
        r = client.post(
            "/v1/ai/proposals/suggest/budget",
            headers=auth_h,
            json={"envelope_id": str(uuid4())},
        )
        assert_problem(r, code="envelope.not_found", status=404)

    def test_no_api_key_returns_structured_error(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_id = _make_envelope(client, auth_h)
        r = client.post(
            "/v1/ai/proposals/suggest/budget",
            headers=auth_h,
            json={"envelope_id": env_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proposal"] is None
        assert "no api key" in (body["error"] or "").lower()
