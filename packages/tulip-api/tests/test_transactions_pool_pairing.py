"""Integration tests for the writer-chokepoint extension (P4.1.a).

Tests that ``POST /v1/transactions``, when given a body whose postings carry
``pool_id``, atomically writes a paired shadow-ledger transaction per
ADR-0001's pairing rule. Covers the full handler chain: pre-flight
validation, lazy system-pool creation, the auto-pairing engine, atomic
commit / rollback, and the audit-log extension.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem
from tulip_storage.models import PoolType, ShadowTxStatus
from tulip_storage.repositories import (
    AllocationPoolRepository,
    ShadowTransactionRepository,
)


@pytest.fixture
def admin_token(client: TestClient) -> tuple[str, str]:
    """Register a household + admin and return (token, household_id)."""
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


@pytest.fixture
def cash_and_food(client: TestClient, auth_h: dict[str, str]) -> tuple[str, str]:
    cash = client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": "Cash", "type": "asset", "currency": "USD", "code": "1110"},
    ).json()
    food = client.post(
        "/v1/accounts",
        headers=auth_h,
        json={"name": "Food", "type": "expense", "currency": "USD", "code": "5100"},
    ).json()
    return cash["id"], food["id"]


def _create_envelope_pool(
    session_maker: sessionmaker[Session],
    household_id: UUID,
    *,
    name: str = "Groceries",
    currency: str = "USD",
) -> UUID:
    """Create an envelope pool directly via the repo (CRUD endpoints land in P4.1.b)."""
    with session_maker() as s:
        pool = AllocationPoolRepository(s, household_id).create(
            pool_type=PoolType.ENVELOPE,
            name=name,
            currency=currency,
        )
        s.commit()
        return pool.id


def _deactivate_pool(session_maker: sessionmaker[Session], household_id: UUID, pool_id: UUID):
    with session_maker() as s:
        AllocationPoolRepository(s, household_id).deactivate(pool_id)
        s.commit()


# ---- Happy path -------------------------------------------------------


class TestSinglePoolSpend:
    def test_creates_paired_shadow_with_two_legs(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        cash, food = cash_and_food
        groceries_pool = _create_envelope_pool(session_maker, household_id)
        body = {
            "date": date.today().isoformat(),
            "description": "Costco",
            "postings": [
                {
                    "account_id": food,
                    "amount": "50",
                    "currency": "USD",
                    "pool_id": str(groceries_pool),
                },
                {"account_id": cash, "amount": "-50", "currency": "USD"},
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        assert r.status_code == 201, r.text
        out = r.json()
        # Paired shadow tx id is returned in the response.
        assert out["paired_shadow_tx_id"] is not None
        # Pool id round-trips on the response posting.
        food_posting = next(p for p in out["postings"] if p["account_id"] == food)
        assert food_posting["pool_id"] == str(groceries_pool)
        cash_posting = next(p for p in out["postings"] if p["account_id"] == cash)
        assert cash_posting["pool_id"] is None

        # Inspect the shadow side directly.
        shadow_id = UUID(out["paired_shadow_tx_id"])
        with session_maker() as s:
            shadow_repo = ShadowTransactionRepository(s, household_id)
            header = shadow_repo.get(shadow_id)
            assert header is not None
            assert header.status is ShadowTxStatus.POSTED
            assert header.paired_main_tx_id == UUID(out["id"])
            assert header.description == "Costco (envelope effects)"
            assert header.date == date.today()

            postings = shadow_repo.list_postings(shadow_id)
            assert len(postings) == 2
            by_pool = {p.pool_id: p.amount for p in postings}
            assert by_pool[groceries_pool] == Decimal("-50")
            # The Spent USD pool was lazy-materialized and got the absorbing leg.
            spent_pool_id = (
                AllocationPoolRepository(s, household_id)
                .get_system_pool(pool_type=PoolType.SPENT, currency="USD")
                .id  # type: ignore[union-attr]
            )
            assert by_pool[spent_pool_id] == Decimal("50")

    def test_get_returns_paired_shadow_tx_id(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # Round-trip through GET — paired_shadow_tx_id must surface there too.
        cash, food = cash_and_food
        groceries_pool = _create_envelope_pool(session_maker, household_id)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Costco",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(groceries_pool),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        tx_id = r.json()["id"]
        get = client.get(f"/v1/transactions/{tx_id}", headers=auth_h)
        assert get.status_code == 200
        assert get.json()["paired_shadow_tx_id"] is not None


class TestMultiPoolSpend:
    def test_one_paired_shadow_with_three_legs(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # ADR-0001 §B: $50 groceries + $30 entertainment + -$80 cash.
        cash = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Cash", "type": "asset", "currency": "USD", "code": "1110"},
        ).json()["id"]
        food = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Food", "type": "expense", "currency": "USD", "code": "5100"},
        ).json()["id"]
        ent = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Entertainment", "type": "expense", "currency": "USD", "code": "5200"},
        ).json()["id"]
        groceries_pool = _create_envelope_pool(session_maker, household_id, name="Groceries")
        ent_pool = _create_envelope_pool(session_maker, household_id, name="Entertainment")
        body = {
            "date": date.today().isoformat(),
            "description": "Costco",
            "postings": [
                {
                    "account_id": food,
                    "amount": "50",
                    "currency": "USD",
                    "pool_id": str(groceries_pool),
                },
                {
                    "account_id": ent,
                    "amount": "30",
                    "currency": "USD",
                    "pool_id": str(ent_pool),
                },
                {"account_id": cash, "amount": "-80", "currency": "USD"},
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        assert r.status_code == 201, r.text
        shadow_id = UUID(r.json()["paired_shadow_tx_id"])
        with session_maker() as s:
            shadow_repo = ShadowTransactionRepository(s, household_id)
            postings = shadow_repo.list_postings(shadow_id)
            assert len(postings) == 3
            by_pool = {p.pool_id: p.amount for p in postings}
            assert by_pool[groceries_pool] == Decimal("-50")
            assert by_pool[ent_pool] == Decimal("-30")
            spent_pool_id = (
                AllocationPoolRepository(s, household_id)
                .get_system_pool(pool_type=PoolType.SPENT, currency="USD")
                .id  # type: ignore[union-attr]
            )
            assert by_pool[spent_pool_id] == Decimal("80")


class TestLazySystemPoolCreation:
    def test_eur_pool_lazy_creates_system_pools(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # Household registers as USD (default), so only USD system pools
        # exist. Posting an EUR transaction with pool_id should lazy-
        # materialize Inflow/Unallocated/Spent EUR.
        cash = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "EUR Cash", "type": "asset", "currency": "EUR", "code": "1120"},
        ).json()["id"]
        travel = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={"name": "Travel", "type": "expense", "currency": "EUR", "code": "5300"},
        ).json()["id"]
        travel_pool = _create_envelope_pool(
            session_maker, household_id, name="Travel", currency="EUR"
        )

        # Pre-condition: EUR system pools don't exist yet.
        with session_maker() as s:
            assert (
                AllocationPoolRepository(s, household_id).get_system_pool(
                    pool_type=PoolType.SPENT, currency="EUR"
                )
                is None
            )

        body = {
            "date": date.today().isoformat(),
            "description": "Paris dinner",
            "postings": [
                {
                    "account_id": travel,
                    "amount": "40",
                    "currency": "EUR",
                    "pool_id": str(travel_pool),
                },
                {"account_id": cash, "amount": "-40", "currency": "EUR"},
            ],
        }
        r = client.post("/v1/transactions", headers=auth_h, json=body)
        assert r.status_code == 201, r.text

        # Post-condition: all three EUR system pools materialized.
        with session_maker() as s:
            repo = AllocationPoolRepository(s, household_id)
            for pt in (PoolType.INFLOW, PoolType.UNALLOCATED, PoolType.SPENT):
                p = repo.get_system_pool(pool_type=pt, currency="EUR")
                assert p is not None
                assert p.is_active


# ---- Pre-flight error matrix ----------------------------------------


class TestPreflightErrors:
    def test_unknown_pool_id_returns_pool_not_found(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
    ):
        cash, food = cash_and_food
        bogus = uuid4()
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Bad pool",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(bogus),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        body_json = assert_problem(r, code="pool.not_found", status=400)
        assert str(bogus) in body_json["detail"]

    def test_inactive_pool_returns_pool_inactive(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        cash, food = cash_and_food
        pool = _create_envelope_pool(session_maker, household_id)
        _deactivate_pool(session_maker, household_id, pool)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Inactive pool",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(pool),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        assert_problem(r, code="pool.inactive", status=400)

    def test_pool_currency_mismatch_returns_400(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        cash, food = cash_and_food
        # USD account, EUR pool — currency mismatch.
        eur_pool = _create_envelope_pool(
            session_maker, household_id, name="EUR Pool", currency="EUR"
        )
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Wrong ccy",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(eur_pool),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        assert_problem(r, code="pool.currency_mismatch", status=400)

    @pytest.mark.parametrize("acct_type", ["asset", "liability", "income", "equity"])
    def test_non_expense_account_with_pool_id_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
        acct_type: str,
    ):
        cash, food = cash_and_food
        pool = _create_envelope_pool(session_maker, household_id)
        # Build a posting with pool_id on an account whose type is NOT expense.
        wrong = client.post(
            "/v1/accounts",
            headers=auth_h,
            json={
                "name": f"Wrong {acct_type}",
                "type": acct_type,
                "currency": "USD",
                "code": f"X{acct_type[:3].upper()}",
            },
        ).json()["id"]
        # Build a balanced 2-leg main tx — note we still need it balanced
        # to even reach the pool pre-flight, so we put the bad pool_id
        # on the wrong-type account but balance it against another acct.
        if acct_type in ("income",):
            # Income posting is credit-shaped (negative); offset with food.
            postings = [
                {
                    "account_id": wrong,
                    "amount": "-50",
                    "currency": "USD",
                    "pool_id": str(pool),
                },
                {"account_id": food, "amount": "50", "currency": "USD"},
            ]
        elif acct_type == "liability":
            # Liability is credit-shaped too.
            postings = [
                {
                    "account_id": wrong,
                    "amount": "-50",
                    "currency": "USD",
                    "pool_id": str(pool),
                },
                {"account_id": food, "amount": "50", "currency": "USD"},
            ]
        else:
            # asset / equity: debit-shaped (positive).
            postings = [
                {
                    "account_id": wrong,
                    "amount": "50",
                    "currency": "USD",
                    "pool_id": str(pool),
                },
                {"account_id": cash, "amount": "-50", "currency": "USD"},
            ]
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": f"Pool on {acct_type}",
                "postings": postings,
            },
        )
        assert_problem(r, code="pool.invalid_account_type_pairing", status=400)

    def test_pool_in_other_household_returns_pool_not_found(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        session_maker: sessionmaker[Session],
    ):
        # Tenant scoping: a pool in some OTHER household must surface as
        # pool.not_found from this household's perspective (not as the
        # generic 500).
        cash, food = cash_and_food
        other_household_id = uuid4()
        with session_maker() as s:
            from tulip_storage.models import Household

            s.add(Household(id=other_household_id, name="Other", base_currency="USD"))
            s.commit()
        other_pool = _create_envelope_pool(session_maker, other_household_id)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Cross-tenant",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(other_pool),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        assert_problem(r, code="pool.not_found", status=400)


# ---- Engine-side rejections (route through transaction.invalid) ------


class TestEngineRejections:
    def test_refund_shaped_pool_effect_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # A negative-amount EXPENSE posting (refund shape) yields a positive
        # net pool effect, which v1 rejects until the REFUND reason ships.
        cash, food = cash_and_food
        pool = _create_envelope_pool(session_maker, household_id)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Refund",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "-50",
                        "currency": "USD",
                        "pool_id": str(pool),
                    },
                    {"account_id": cash, "amount": "50", "currency": "USD"},
                ],
            },
        )
        assert_problem(r, code="transaction.invalid", status=400)


# ---- Period gate ordering -------------------------------------------


class TestPeriodGate:
    def test_pool_tagged_tx_in_closed_period_rejected(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # Period gate fires inside post_transaction, which runs AFTER
        # pool pre-flight but BEFORE shadow build. Result: period.closed
        # surfaces, no shadow tx is written.
        cash, food = cash_and_food
        pool = _create_envelope_pool(session_maker, household_id)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": "1999-01-01",  # outside the seeded current-year period
                "description": "Time travel",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(pool),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        assert_problem(r, code="period.closed", status=400)
        # Belt-and-braces: no shadow tx exists for this household.
        with session_maker() as s:
            from sqlalchemy import select

            from tulip_storage.models import ShadowTransaction

            count = (
                s.execute(
                    select(ShadowTransaction).where(
                        ShadowTransaction.household_id == household_id,
                    )
                )
                .scalars()
                .all()
            )
            assert len(count) == 0


# ---- Mixed / regression ---------------------------------------------


class TestMixedAndRegression:
    def test_mixed_tagged_and_untagged_postings(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # Two food postings, only one tagged. Shadow tx has 1 pool leg + 1 absorbing.
        cash, food = cash_and_food
        pool = _create_envelope_pool(session_maker, household_id)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Mixed",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "30",
                        "currency": "USD",
                        "pool_id": str(pool),
                    },
                    {
                        "account_id": food,
                        "amount": "20",
                        "currency": "USD",
                        # untagged
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        assert r.status_code == 201, r.text
        shadow_id = UUID(r.json()["paired_shadow_tx_id"])
        with session_maker() as s:
            postings = ShadowTransactionRepository(s, household_id).list_postings(shadow_id)
            assert len(postings) == 2  # only the tagged leg → shadow leg, plus absorbing
            by_pool = {p.pool_id: p.amount for p in postings}
            assert by_pool[pool] == Decimal("-30")

    def test_no_pool_tagged_postings_writes_no_shadow_tx(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        # Regression: existing behavior unchanged when no pool_id.
        cash, food = cash_and_food
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Plain",
                "postings": [
                    {"account_id": food, "amount": "12.50", "currency": "USD"},
                    {"account_id": cash, "amount": "-12.50", "currency": "USD"},
                ],
            },
        )
        assert r.status_code == 201
        out = r.json()
        assert out["paired_shadow_tx_id"] is None
        # Confirm no shadow tx exists.
        with session_maker() as s:
            assert (
                ShadowTransactionRepository(s, household_id).get_paired_id_for_main_tx(
                    UUID(out["id"])
                )
                is None
            )


# ---- Audit log ------------------------------------------------------


class TestAuditLog:
    def test_paired_shadow_tx_id_captured_in_audit_after(
        self,
        client: TestClient,
        auth_h: dict[str, str],
        cash_and_food: tuple[str, str],
        household_id: UUID,
        session_maker: sessionmaker[Session],
    ):
        cash, food = cash_and_food
        pool = _create_envelope_pool(session_maker, household_id)
        r = client.post(
            "/v1/transactions",
            headers=auth_h,
            json={
                "date": date.today().isoformat(),
                "description": "Audit",
                "postings": [
                    {
                        "account_id": food,
                        "amount": "50",
                        "currency": "USD",
                        "pool_id": str(pool),
                    },
                    {"account_id": cash, "amount": "-50", "currency": "USD"},
                ],
            },
        )
        tx_id = UUID(r.json()["id"])
        shadow_tx_id = r.json()["paired_shadow_tx_id"]

        with session_maker() as s:
            from sqlalchemy import select

            from tulip_storage.models import AuditLog

            row = s.execute(
                select(AuditLog).where(
                    AuditLog.household_id == household_id,
                    AuditLog.entity_type == "transaction",
                    AuditLog.entity_id == tx_id,
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.action == "create"
            assert row.after_snapshot is not None
            assert row.after_snapshot["paired_shadow_tx_id"] == shadow_tx_id
