"""Regression + property coverage for SQLite decimal storage (#395).

SQLAlchemy's ``Numeric(20, 8)`` on SQLite silently degrades Decimal values
to IEEE-754 floats. Two-leg transactions (``+x + -x``) sum to exactly 0
because the floats share a magnitude, but three-or-more-leg splits
``+T + Σ(-split_i)`` carry a rounding residue that trips the
``trg_transactions_balanced_on_post`` trigger with
``HAVING SUM(amount) != 0``.

The fix is a :class:`tulip_storage.models.base.SqliteDecimal` TypeDecorator
that stores Decimal as scaled INT64 on SQLite — SQLite sums integers
exactly. Postgres NUMERIC arithmetic is already exact, so the decorator
is a no-op there.

These tests:

* Lock the regression at the repository layer (`test_split_sum_drift_*`).
* Pin the bind/result behaviour of the decorator itself.
* Use Hypothesis to generalise: any list of Decimal postings that sums
  to 0 must round-trip + re-aggregate to 0 with no spurious trigger fire.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.orm import Session

from tulip_core.money import Money
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import (
    Account as AccountModel,
)
from tulip_storage.models import (
    AccountType,
    Household,
    PeriodStatus,
)
from tulip_storage.models.base import SqliteDecimal
from tulip_storage.repositories import (
    AccountRepository,
    PeriodRepository,
    TransactionRepository,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


def _seed_accounts(session: Session, household: Household, count: int) -> list[AccountModel]:
    """Create ``count`` USD accounts and an open 2026 period."""
    repo = AccountRepository(session, household.id)
    accounts = [
        repo.create(
            code=f"{1000 + i}",
            name=f"Account {i}",
            type=AccountType.ASSET if i == 0 else AccountType.EXPENSE,
            currency="USD",
        )
        for i in range(count)
    ]
    PeriodRepository(session, household.id).create(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=PeriodStatus.OPEN,
    )
    session.commit()
    return accounts


def _save_balanced_posted(
    session: Session, household: Household, accounts: list[AccountModel], amounts: list[Decimal]
) -> None:
    """Build a balanced multi-leg POSTED transaction and persist it."""
    assert len(accounts) == len(amounts), "one account per posting"
    domain_tx = DomainTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 6, 1),
        description="multi-leg split",
        postings=tuple(
            DomainPosting(
                id=uuid4(),
                account_id=acct.id,
                amount=Money(amt, "USD"),
            )
            for acct, amt in zip(accounts, amounts, strict=True)
        ),
        status=DomainTxStatus.POSTED,
    )
    TransactionRepository(session, household.id).save_balanced(domain_tx)
    session.commit()


class TestSplitBalanceRegression:
    """Replays of QIF lines that triggered #395 before the fix."""

    def test_split_sum_drift_three_way_negative(
        self, session: Session, household: Household
    ) -> None:
        """Line 117 from the user's failing batch: T=-62.01 / splits -48.29, -13.72.

        IEEE-754: ``-62.01 + 48.29 + 13.72 == 1.78e-15`` (not 0). Exact
        decimal arithmetic gives 0. The trigger must agree with exact
        decimal arithmetic.
        """
        bank, gas, warranty = _seed_accounts(session, household, count=3)
        _save_balanced_posted(
            session,
            household,
            [bank, gas, warranty],
            [Decimal("-62.01"), Decimal("48.29"), Decimal("13.72")],
        )

    def test_split_sum_drift_ten_way_payroll(self, session: Session, household: Household) -> None:
        """Line 99: 10-leg payroll split (T=+3745.26 with 9 deductions + 1 net).

        ``sum([3745.26, -448.90, -327.80, -259.07, -76.66, 0.0, -211.48,
        -217.90, -52.87, 5287.07, 52.87])`` ≈ 4.83e-13 in IEEE-754.
        Exact decimal arithmetic gives 0.
        """
        accounts = _seed_accounts(session, household, count=11)
        amounts = [
            Decimal("3745.26"),
            Decimal("-448.90"),
            Decimal("-327.80"),
            Decimal("-259.07"),
            Decimal("-76.66"),
            Decimal("0.00"),
            Decimal("-211.48"),
            Decimal("-217.90"),
            Decimal("-52.87"),
            Decimal("5287.07"),
            Decimal("-7437.65"),
        ]
        assert sum(amounts) == Decimal("0"), "test fixture must balance"
        _save_balanced_posted(session, household, accounts, amounts)

    def test_postings_typeof_is_integer_not_real(
        self, session: Session, household: Household
    ) -> None:
        """After the fix, ``postings.amount`` must be stored as INTEGER.

        SQLite's per-row dynamic typing means each value carries its own
        ``typeof()``. If any row still binds as ``real`` the trigger will
        re-introduce IEEE-754 drift the moment it sums across that row.
        """
        bank, food = _seed_accounts(session, household, count=2)
        _save_balanced_posted(
            session,
            household,
            [bank, food],
            [Decimal("-12.34"), Decimal("12.34")],
        )
        rows = session.execute(text("SELECT typeof(amount) FROM postings")).scalars().all()
        assert rows, "expected the postings we just inserted"
        assert all(t == "integer" for t in rows), (
            f"postings.amount must be stored as INTEGER (got {set(rows)}) — "
            "SqliteDecimal regressed back to REAL storage"
        )


class TestSqliteDecimalTypeDecorator:
    """Pin the bind/result semantics of the decorator in isolation."""

    @pytest.mark.parametrize(
        ("value", "expected_int"),
        [
            (Decimal("0"), 0),
            (Decimal("1"), 100_000_000),
            (Decimal("-62.01"), -6_201_000_000),
            (Decimal("0.00000001"), 1),
            (Decimal("12345.67890123"), 1_234_567_890_123),
        ],
    )
    def test_bind_scales_decimal_to_int_on_sqlite(self, value: Decimal, expected_int: int) -> None:
        from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

        dec = SqliteDecimal()
        bound = dec.process_bind_param(value, sqlite_dialect())
        assert bound == expected_int
        assert isinstance(bound, int)

    def test_bind_quantizes_subscale_with_half_even(self) -> None:
        from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

        # 9-decimal-places value at scale=8 → quantized to 8dp banker's-rounded.
        dec = SqliteDecimal()
        bound = dec.process_bind_param(Decimal("0.000000005"), sqlite_dialect())
        # 0.000000005 → rounds to even (0.00000000) at 8dp
        assert bound == 0
        bound = dec.process_bind_param(Decimal("0.000000015"), sqlite_dialect())
        # 0.000000015 → rounds to even (0.00000002) at 8dp
        assert bound == 2

    @pytest.mark.parametrize(
        ("stored_int", "expected"),
        [
            (0, Decimal("0")),
            (100_000_000, Decimal("1")),
            (-6_201_000_000, Decimal("-62.01")),
            (1, Decimal("0.00000001")),
        ],
    )
    def test_result_unscales_int_to_decimal_on_sqlite(
        self, stored_int: int, expected: Decimal
    ) -> None:
        from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

        dec = SqliteDecimal()
        out = dec.process_result_value(stored_int, sqlite_dialect())
        assert out == expected
        assert isinstance(out, Decimal)

    def test_none_round_trips(self) -> None:
        from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

        dec = SqliteDecimal()
        assert dec.process_bind_param(None, sqlite_dialect()) is None
        assert dec.process_result_value(None, sqlite_dialect()) is None

    def test_postgres_dialect_passes_decimal_through(self) -> None:
        """On PG/MySQL the decorator is a no-op — NUMERIC is already exact."""
        from sqlalchemy.dialects.postgresql import dialect as pg_dialect

        dec = SqliteDecimal()
        value = Decimal("123.45")
        assert dec.process_bind_param(value, pg_dialect()) == value
        assert dec.process_result_value(value, pg_dialect()) == value


@pytest.mark.property
class TestSqliteDecimalProperty:
    """Hypothesis-driven: any balanced list of Decimals round-trips to 0."""

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        amounts=st.lists(
            st.decimals(
                min_value=Decimal("-1000000"),
                max_value=Decimal("1000000"),
                allow_nan=False,
                allow_infinity=False,
                places=2,
            ),
            min_size=2,
            max_size=10,
        )
    )
    def test_balanced_postings_pass_trigger(
        self, session: Session, household: Household, amounts: list[Decimal]
    ) -> None:
        # Force the list to balance: append the negation of the partial sum.
        amounts = list(amounts)
        amounts.append(-sum(amounts))
        accounts = _seed_accounts(session, household, count=len(amounts))
        _save_balanced_posted(session, household, accounts, amounts)
