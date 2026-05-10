"""Integration tests for /v1/pools — transfer + budget-inflow (P4.1.b)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

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
    return r2.json()["access_token"], household_id


@pytest.fixture
def auth_h(admin_token: tuple[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token[0]}"}


@pytest.fixture
def household_id(admin_token: tuple[str, str]) -> UUID:
    return UUID(admin_token[1])


def _make_envelope(
    client: TestClient,
    auth_h: dict[str, str],
    name: str,
    currency: str = "USD",
) -> str:
    return client.post(
        "/v1/envelopes",
        headers=auth_h,
        json={
            "name": name,
            "currency": currency,
            "budget_period": "monthly",
            "rollover_policy": "reset",
        },
    ).json()["id"]


# ---- Budget inflow ----------------------------------------------------


class TestBudgetInflow:
    def test_inflow_lazy_creates_eur_system_pools(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # Pre-condition: only USD system pools exist (created at register).
        from tulip_storage.models import PoolType
        from tulip_storage.repositories import AllocationPoolRepository

        with session_maker() as s:
            assert (
                AllocationPoolRepository(s, household_id).get_system_pool(
                    pool_type=PoolType.SPENT, currency="EUR"
                )
                is None
            )

        r = client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "1500.00",
                "currency": "EUR",
                "date": date.today().isoformat(),
                "description": "Q1 salary",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["currency"] == "EUR"
        assert Decimal(body["balance"]) == Decimal("1500.00")

        # Post-condition: all three EUR system pools materialized.
        with session_maker() as s:
            repo = AllocationPoolRepository(s, household_id)
            for pt in (PoolType.INFLOW, PoolType.UNALLOCATED, PoolType.SPENT):
                p = repo.get_system_pool(pool_type=pt, currency="EUR")
                assert p is not None
                assert p.is_active

    def test_inflow_unknown_currency_rejected(self, client: TestClient, auth_h: dict[str, str]):
        r = client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "100",
                "currency": "ZZZ",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        assert_problem(r, code="pool.inflow_currency_unknown", status=400)

    def test_inflow_negative_amount_rejected_at_schema(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        r = client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "-100",
                "currency": "USD",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        assert_problem(r, code="validation.failed", status=422)


# ---- Transfer ---------------------------------------------------------


class TestTransfer:
    def test_transfer_between_two_envelopes(
        self,
        client: TestClient,
        auth_h: dict[str, str],
    ):
        # Seed: budget-inflow $500, refill envelope A with $300, then move $100 to envelope B.
        client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "500",
                "currency": "USD",
                "date": date.today().isoformat(),
                "description": "Inflow",
            },
        )
        env_a = _make_envelope(client, auth_h, "Groceries")
        env_b = _make_envelope(client, auth_h, "Entertainment")
        client.post(
            f"/v1/envelopes/{env_a}/refill",
            headers=auth_h,
            json={
                "amount": "300",
                "date": date.today().isoformat(),
                "description": "Refill A",
            },
        )

        r = client.post(
            f"/v1/pools/{env_a}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": env_b,
                "amount": "100",
                "date": date.today().isoformat(),
                "description": "Move to entertainment",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["pool_id"] == env_b
        assert Decimal(body["balance"]) == Decimal("100.00")

        # And source is reduced.
        bal_a = client.get(f"/v1/envelopes/{env_a}/balance", headers=auth_h).json()
        assert Decimal(bal_a["balance"]) == Decimal("200.00")

    def test_transfer_same_pool_rejected(self, client: TestClient, auth_h: dict[str, str]):
        env = _make_envelope(client, auth_h, "X")
        r = client.post(
            f"/v1/pools/{env}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": env,
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Self",
            },
        )
        assert_problem(r, code="pool.transfer_same_pool", status=400)

    def test_transfer_currency_mismatch_rejected(self, client: TestClient, auth_h: dict[str, str]):
        usd_env = _make_envelope(client, auth_h, "USD env")
        eur_env = _make_envelope(client, auth_h, "EUR env", currency="EUR")
        r = client.post(
            f"/v1/pools/{usd_env}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": eur_env,
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Cross-ccy",
            },
        )
        assert_problem(r, code="pool.transfer_currency_mismatch", status=400)

    def test_transfer_with_system_pool_source_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        from tulip_storage.models import PoolType
        from tulip_storage.repositories import AllocationPoolRepository

        with session_maker() as s:
            unallocated = AllocationPoolRepository(s, household_id).get_system_pool(
                pool_type=PoolType.UNALLOCATED, currency="USD"
            )
            assert unallocated is not None
            unalloc_id = str(unallocated.id)
        env = _make_envelope(client, auth_h, "Dest")

        r = client.post(
            f"/v1/pools/{unalloc_id}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": env,
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        body = assert_problem(r, code="pool.transfer_system_pool_forbidden", status=400)
        assert body["role"] == "source"

    def test_transfer_with_system_pool_destination_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        from tulip_storage.models import PoolType
        from tulip_storage.repositories import AllocationPoolRepository

        with session_maker() as s:
            unallocated = AllocationPoolRepository(s, household_id).get_system_pool(
                pool_type=PoolType.UNALLOCATED, currency="USD"
            )
            assert unallocated is not None
            unalloc_id = str(unallocated.id)
        env = _make_envelope(client, auth_h, "Source")

        r = client.post(
            f"/v1/pools/{env}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": unalloc_id,
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        body = assert_problem(r, code="pool.transfer_system_pool_forbidden", status=400)
        assert body["role"] == "destination"

    def test_transfer_unknown_source_returns_400(self, client: TestClient, auth_h: dict[str, str]):
        # pool.not_found is a 400 (introduced for the chokepoint in P4.1.a,
        # where the pool is referenced inside a posting body). Reused here
        # for consistency: clients dispatch on `code`, not status.
        env = _make_envelope(client, auth_h, "Dest")
        r = client.post(
            f"/v1/pools/{uuid4()}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": env,
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        assert_problem(r, code="pool.not_found", status=400)

    def test_transfer_unknown_dest_returns_400(self, client: TestClient, auth_h: dict[str, str]):
        env = _make_envelope(client, auth_h, "Source")
        r = client.post(
            f"/v1/pools/{env}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": str(uuid4()),
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        assert_problem(r, code="pool.not_found", status=400)

    def test_transfer_inactive_source_rejected(self, client: TestClient, auth_h: dict[str, str]):
        src = _make_envelope(client, auth_h, "Dead")
        dest = _make_envelope(client, auth_h, "Alive")
        client.delete(f"/v1/envelopes/{src}", headers=auth_h)
        r = client.post(
            f"/v1/pools/{src}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": dest,
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "Bad",
            },
        )
        assert_problem(r, code="pool.inactive", status=400)

    def test_transfer_envelope_to_sinking_fund_allowed(
        self, client: TestClient, auth_h: dict[str, str]
    ):
        client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "500",
                "currency": "USD",
                "date": date.today().isoformat(),
                "description": "Inflow",
            },
        )
        env = _make_envelope(client, auth_h, "Source")
        client.post(
            f"/v1/envelopes/{env}/refill",
            headers=auth_h,
            json={
                "amount": "300",
                "date": date.today().isoformat(),
                "description": "Refill",
            },
        )
        sf = client.post(
            "/v1/sinking-funds",
            headers=auth_h,
            json={
                "name": "Vacation",
                "currency": "USD",
                "target_amount": "3000",
                "target_date": date(date.today().year + 1, 1, 1).isoformat(),
                "contribution_strategy": "manual",
            },
        ).json()["id"]
        r = client.post(
            f"/v1/pools/{env}/transfer",
            headers=auth_h,
            json={
                "dest_pool_id": sf,
                "amount": "100",
                "date": date.today().isoformat(),
                "description": "Move to vacation",
            },
        )
        assert r.status_code == 201, r.text
        # Check sinking fund balance.
        bal = client.get(f"/v1/sinking-funds/{sf}/balance", headers=auth_h).json()
        assert Decimal(bal["balance"]) == Decimal("100.00")


class TestBatchedBalances:
    """``POST /v1/pools/balances`` (#137) — batched balance lookup."""

    def test_returns_balances_for_known_pools(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        env_a = _make_envelope(client, auth_h, "EnvA")
        env_b = _make_envelope(client, auth_h, "EnvB")

        # Seed inflow + transfer some into EnvA so its balance is non-zero.
        client.post(
            "/v1/pools/budget-inflow",
            headers=auth_h,
            json={
                "amount": "500",
                "currency": "USD",
                "date": date.today().isoformat(),
                "description": "Salary",
            },
        ).raise_for_status()
        client.post(
            f"/v1/envelopes/{env_a}/refill",
            headers=auth_h,
            json={
                "amount": "120",
                "date": date.today().isoformat(),
                "description": "manual refill",
            },
        ).raise_for_status()

        r = client.post(
            "/v1/pools/balances",
            headers=auth_h,
            json={"pool_ids": [env_a, env_b]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        by_id = {row["pool_id"]: row for row in body}
        assert Decimal(by_id[env_a]["balance"]) == Decimal("120.00")
        assert Decimal(by_id[env_b]["balance"]) == Decimal("0.00")
        assert by_id[env_a]["currency"] == "USD"
        assert by_id[env_a]["name"] == "EnvA"

    def test_empty_pool_ids_returns_empty_list(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        r = client.post("/v1/pools/balances", headers=auth_h, json={"pool_ids": []})
        assert r.status_code == 200
        assert r.json() == []

    def test_silently_drops_unknown_ids(self, client: TestClient, auth_h: dict[str, str]) -> None:
        env = _make_envelope(client, auth_h, "Real")
        r = client.post(
            "/v1/pools/balances",
            headers=auth_h,
            json={"pool_ids": [env, str(uuid4())]},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["pool_id"] == env

    def test_other_household_pools_are_invisible(
        self, client: TestClient, auth_h: dict[str, str]
    ) -> None:
        """Tenant scoping: a pool created in another household is silently dropped."""
        # Make the first household's envelope.
        env_mine = _make_envelope(client, auth_h, "Mine")

        # Register a second household with its own envelope.
        client.post(
            "/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "correct horse battery staple",
                "display_name": "Other",
                "household_name": "Other",
            },
        ).raise_for_status()
        other_login = client.post(
            "/v1/auth/login",
            json={"email": "other@example.com", "password": "correct horse battery staple"},
        ).json()
        other_h = {"Authorization": f"Bearer {other_login['access_token']}"}
        env_theirs = _make_envelope(client, other_h, "Theirs")

        # Caller A asks for both ids; only its own appears.
        r = client.post(
            "/v1/pools/balances",
            headers=auth_h,
            json={"pool_ids": [env_mine, env_theirs]},
        )
        assert r.status_code == 200
        ids = [row["pool_id"] for row in r.json()]
        assert ids == [env_mine]

    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.post("/v1/pools/balances", json={"pool_ids": []})
        assert r.status_code == 401


class TestPoolUnauthenticated:
    def test_inflow_without_token_returns_401(self, client: TestClient):
        r = client.post(
            "/v1/pools/budget-inflow",
            json={
                "amount": "10",
                "currency": "USD",
                "date": date.today().isoformat(),
                "description": "X",
            },
        )
        assert r.status_code == 401

    def test_transfer_without_token_returns_401(self, client: TestClient):
        r = client.post(
            f"/v1/pools/{uuid4()}/transfer",
            json={
                "dest_pool_id": str(uuid4()),
                "amount": "10",
                "date": date.today().isoformat(),
                "description": "X",
            },
        )
        assert r.status_code == 401
