"""Performance baselines for hot ledger paths.

These guard against egregious (>2-3x) regressions on the three queries
that will hurt first as Phase 5 import volume grows: posting a
transaction, balancing a single account, and computing the full trial
balance.

The wall-clock budgets below are deliberately generous (roughly 2-3x
typical CI mean) to absorb runner noise without false-failing PRs.
Thresholds live in this file rather than a checked-in baseline JSON
because pytest-benchmark keys baselines on machine fingerprint —
committing a Mac baseline wouldn't match Linux CI runners. Hard
thresholds are simpler, portable across machines, and fail with a
readable assertion message.

Run locally:
    just bench
or directly:
    uv run pytest -m benchmark --benchmark-only

The benchmark suite is excluded from the default `pytest` loop via the
`-m 'not benchmark'` selector in `pyproject.toml [tool.pytest.ini_options]
addopts`, and is also incompatible with `pytest-xdist` parallelism — run
it sequentially.

If a benchmark trips its threshold legitimately (you've added work that
deserves the budget), update the *_BUDGET_S constant in the same PR
that introduces the work, with a one-line comment explaining why.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
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
from tulip_storage.models import AccountType, Household, PeriodStatus
from tulip_storage.repositories import (
    AccountRepository,
    PeriodRepository,
    TransactionRepository,
)

# Wall-clock budgets in seconds. Each is roughly 2-3x the typical mean
# observed on the GitHub Actions Linux runner — enough headroom to
# absorb noise, tight enough to catch any actual >2x regression.
POST_BUDGET_S = 0.05
BALANCE_BUDGET_S = 0.02
TRIAL_BALANCE_BUDGET_S = 0.05

pytestmark = pytest.mark.benchmark


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Bench", base_currency="USD")
    session.add(h)
    session.commit()
    return h


@pytest.fixture
def accounts(session: Session, household: Household) -> tuple[AccountModel, AccountModel]:
    repo = AccountRepository(session, household.id)
    cash = repo.create(code="1110", name="Cash", type=AccountType.ASSET, currency="USD")
    food = repo.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")
    session.commit()
    return cash, food


@pytest.fixture
def open_period(session: Session, household: Household) -> None:
    PeriodRepository(session, household.id).create(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=PeriodStatus.OPEN,
    )
    session.commit()


def _make_tx(
    household: Household,
    debit: AccountModel,
    credit: AccountModel,
    amount: Decimal,
) -> DomainTransaction:
    return DomainTransaction(
        id=uuid4(),
        household_id=household.id,
        date=date(2026, 6, 1),
        description="bench",
        postings=(
            DomainPosting(id=uuid4(), account_id=debit.id, amount=Money(amount, "USD")),
            DomainPosting(id=uuid4(), account_id=credit.id, amount=Money(-amount, "USD")),
        ),
        status=DomainTxStatus.POSTED,
    )


def test_post_transaction(
    benchmark,
    session: Session,
    household: Household,
    accounts: tuple[AccountModel, AccountModel],
    open_period: None,
) -> None:
    """One balanced two-posting transaction (insert, flush, update, commit)."""
    cash, food = accounts
    repo = TransactionRepository(session, household.id)

    def post_one() -> None:
        repo.save_balanced(_make_tx(household, food, cash, Decimal("12.50")))
        session.commit()

    benchmark.pedantic(post_one, rounds=10, iterations=1, warmup_rounds=1)
    mean = benchmark.stats.stats.mean
    assert mean < POST_BUDGET_S, (
        f"save_balanced too slow: mean={mean:.4f}s > budget={POST_BUDGET_S}s"
    )


def test_balance_for_account(
    benchmark,
    session: Session,
    household: Household,
    accounts: tuple[AccountModel, AccountModel],
    open_period: None,
) -> None:
    """Single-account balance query against a populated 100-tx ledger."""
    cash, food = accounts
    repo = TransactionRepository(session, household.id)
    for _ in range(100):
        repo.save_balanced(_make_tx(household, food, cash, Decimal("1.00")))
    session.commit()

    def balance() -> Decimal:
        return repo.balance_for_account(cash.id, currency="USD")

    benchmark.pedantic(balance, rounds=20, iterations=1, warmup_rounds=2)
    mean = benchmark.stats.stats.mean
    assert mean < BALANCE_BUDGET_S, (
        f"balance_for_account too slow: mean={mean:.4f}s > budget={BALANCE_BUDGET_S}s"
    )


def test_trial_balance(
    benchmark,
    session: Session,
    household: Household,
    accounts: tuple[AccountModel, AccountModel],
    open_period: None,
) -> None:
    """Trial balance over a populated ledger (9 accounts, 10 txs each, 80 postings)."""
    acct_repo = AccountRepository(session, household.id)
    extras: list[AccountModel] = []
    for i in range(8):
        extras.append(
            acct_repo.create(
                code=f"6{i:03d}",
                name=f"Bench-{i}",
                type=AccountType.EXPENSE,
                currency="USD",
            )
        )
    session.commit()

    cash, _food = accounts
    tx_repo = TransactionRepository(session, household.id)
    for acct in extras:
        for _ in range(10):
            tx_repo.save_balanced(_make_tx(household, acct, cash, Decimal("1.00")))
    session.commit()

    def trial() -> list:
        return tx_repo.trial_balance()

    benchmark.pedantic(trial, rounds=20, iterations=1, warmup_rounds=2)
    mean = benchmark.stats.stats.mean
    assert mean < TRIAL_BALANCE_BUDGET_S, (
        f"trial_balance too slow: mean={mean:.4f}s > budget={TRIAL_BALANCE_BUDGET_S}s"
    )
