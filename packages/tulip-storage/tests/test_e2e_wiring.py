"""End-to-end wiring test crossing tulip-core ↔ tulip-storage.

Drives a complete flow:

  alembic upgrade head
  → seed household, period, accounts (multi-currency)
  → build a Transaction in tulip-core (balanced via engine helper)
  → post_transaction (engine validates period + balance)
  → persist tx + postings to SQLite, promoting status to POSTED
  → query trial balance per currency and assert it sums to zero

This is the proof that the Phase-1 layers compose cleanly and that the
engine's contract is the same one the storage layer enforces.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from tulip_core.accounting import balance_with_fx_postings, post_transaction
from tulip_core.money import Money
from tulip_core.periods import Period as DomainPeriod
from tulip_core.periods import PeriodStatus as DomainPeriodStatus
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
    Account,
    AccountType,
    Household,
    Period,
    PeriodStatus,
    Posting,
    Transaction,
    TransactionStatus,
)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(ALEMBIC_INI.parent / "src" / "tulip_storage" / "migrations"),
    )
    return cfg


@pytest.fixture
def db(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tulip.db'}"
    upgrade(_make_alembic_cfg(db_url), "head")
    eng = create_engine(db_url, future=True)

    @event.listens_for(eng, "connect")
    def _fk(dc, _r):  # type: ignore[no-untyped-def]
        c = dc.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    yield sessionmaker(eng, expire_on_commit=False)
    eng.dispose()


def test_e2e_post_multi_currency_balanced_via_fx_helper(db) -> None:
    Smaker = db
    household_id = uuid4()
    period_id = uuid4()
    cash_id = uuid4()
    expense_id = uuid4()
    fx_acct_id = uuid4()

    # ---- seed ----
    with Smaker() as s:
        s.add(Household(id=household_id, name="Smith", base_currency="USD"))
        s.add(
            Period(
                household_id=household_id,
                id=period_id,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                status=PeriodStatus.OPEN,
            )
        )
        s.add_all(
            [
                Account(
                    household_id=household_id,
                    id=cash_id,
                    code="1110",
                    name="Checking",
                    type=AccountType.ASSET,
                    currency="USD",
                    visibility="shared",
                ),
                Account(
                    household_id=household_id,
                    id=expense_id,
                    code="5100",
                    name="EUR vendor",
                    type=AccountType.EXPENSE,
                    currency="EUR",
                    visibility="shared",
                ),
                Account(
                    household_id=household_id,
                    id=fx_acct_id,
                    code="3900",
                    name="FX gain/loss",
                    type=AccountType.EQUITY,
                    currency="USD",
                    visibility="shared",
                ),
            ]
        )
        s.commit()

    # ---- core: build, balance, validate ----
    domain_period = DomainPeriod(
        id=period_id,
        household_id=household_id,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=DomainPeriodStatus.OPEN,
    )
    unbalanced = DomainTransaction(
        id=uuid4(),
        household_id=household_id,
        date=date(2026, 6, 1),
        description="Pay EUR vendor with USD",
        postings=(
            DomainPosting(
                id=uuid4(),
                account_id=cash_id,
                amount=Money(Decimal("-110.00"), "USD"),
            ),
            DomainPosting(
                id=uuid4(),
                account_id=expense_id,
                amount=Money(Decimal("100.00"), "EUR"),
            ),
        ),
        status=DomainTxStatus.PENDING,
    )
    balanced = balance_with_fx_postings(
        unbalanced, fx_gain_loss_account_id=fx_acct_id, base_currency="USD"
    )
    posted = post_transaction(balanced, periods=[domain_period])
    assert posted.status is DomainTxStatus.POSTED

    # ---- persist ----
    with Smaker() as s:
        s.add(
            Transaction(
                household_id=household_id,
                id=posted.id,
                date=posted.date,
                description=posted.description,
                status=TransactionStatus.PENDING,
            )
        )
        s.flush()
        s.add_all(
            [
                Posting(
                    id=p.id,
                    household_id=household_id,
                    transaction_id=posted.id,
                    account_id=p.account_id,
                    amount=p.amount.amount,
                    currency=p.amount.currency,
                    memo=p.memo,
                )
                for p in posted.postings
            ]
        )
        s.flush()
        # Trigger validates balance per currency on the status transition.
        s.execute(
            Transaction.__table__.update()
            .where(Transaction.id == posted.id)
            .values(status=TransactionStatus.POSTED.value)
        )
        s.commit()

    # ---- query trial balance per currency ----
    with Smaker() as s:
        per_currency = dict(
            s.execute(
                select(Posting.currency, func.sum(Posting.amount))
                .where(Posting.transaction_id == posted.id)
                .group_by(Posting.currency)
            ).all()
        )
    assert per_currency == {"USD": Decimal("0"), "EUR": Decimal("0")}
