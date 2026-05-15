"""Income-statement report (P7.1).

Revenue (income accounts) - expenses over a date range. Optional
comparison period reproduces the same shape side-by-side.

Ledger convention: income accounts have negative balances (credits),
expense accounts have positive balances (debits). The renderer flips
income signs so positive numbers represent "money in" in both columns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from tulip_reports.engine import get_renderer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class IncomeStatementRow:
    """One line: an income or expense account, summed over the period."""

    code: str | None
    name: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class IncomeStatementSection:
    """Revenue or Expenses, with per-currency subtotals."""

    title: str
    rows: list[IncomeStatementRow]
    subtotals_by_currency: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class IncomeStatementPeriod:
    """One period's revenue + expenses + net income."""

    start: date_type
    end: date_type
    revenue: IncomeStatementSection
    expenses: IncomeStatementSection
    net_income_by_currency: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class IncomeStatementData:
    """Everything the income-statement template needs to render."""

    current_period: IncomeStatementPeriod
    prior_period: IncomeStatementPeriod | None
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


VisibleAccountFilter = Callable[[str, UUID | None], bool]


def _build_period(
    session: Session,
    *,
    household_id: UUID,
    start: date_type,
    end: date_type,
    visible_account_filter: VisibleAccountFilter | None,
) -> IncomeStatementPeriod:
    """Compute one period's revenue / expense sections."""
    from tulip_storage.repositories import AccountRepository, TransactionRepository

    tx_repo = TransactionRepository(session, household_id)
    account_repo = AccountRepository(session, household_id)
    accounts_by_id = {a.id: a for a in account_repo.list_active()}

    # Compute balances at end of period minus balances at start of period.
    # Trial balance returns running balances; the period delta is the
    # difference between the two snapshots.
    end_rows = {
        (r.account_id, r.currency): Decimal(str(r.balance))
        for r in tx_repo.trial_balance(as_of=end)
    }
    if start > date_type.min:
        prior_day = date_type.fromordinal(max(1, start.toordinal() - 1))
        start_rows = {
            (r.account_id, r.currency): Decimal(str(r.balance))
            for r in tx_repo.trial_balance(as_of=prior_day)
        }
    else:
        start_rows = {}

    revenue_rows: list[IncomeStatementRow] = []
    expense_rows: list[IncomeStatementRow] = []

    for (account_id, currency), end_balance in end_rows.items():
        delta = end_balance - start_rows.get((account_id, currency), Decimal("0"))
        if delta == 0:
            continue
        a = accounts_by_id.get(account_id)
        if a is None:
            continue
        if visible_account_filter is not None and not visible_account_filter(
            a.visibility, a.created_by_user_id
        ):
            continue
        type_value = a.type.value
        if type_value == "income":
            # Income flows credit-side (negative); flip so revenue is positive.
            revenue_rows.append(
                IncomeStatementRow(
                    code=a.code,
                    name=a.name,
                    amount=(-delta).quantize(Decimal("0.01")),
                    currency=currency,
                )
            )
        elif type_value == "expense":
            expense_rows.append(
                IncomeStatementRow(
                    code=a.code,
                    name=a.name,
                    amount=delta.quantize(Decimal("0.01")),
                    currency=currency,
                )
            )

    def _section(title: str, rows: list[IncomeStatementRow]) -> IncomeStatementSection:
        subtotals: dict[str, Decimal] = {}
        for row in rows:
            subtotals[row.currency] = subtotals.get(row.currency, Decimal("0")) + row.amount
        return IncomeStatementSection(
            title=title,
            rows=sorted(rows, key=lambda r: (r.code or "", r.name)),
            subtotals_by_currency=subtotals,
        )

    revenue = _section("Revenue", revenue_rows)
    expenses = _section("Expenses", expense_rows)
    net: dict[str, Decimal] = {}
    for currency in set(revenue.subtotals_by_currency) | set(expenses.subtotals_by_currency):
        net[currency] = revenue.subtotals_by_currency.get(
            currency, Decimal("0")
        ) - expenses.subtotals_by_currency.get(currency, Decimal("0"))
    return IncomeStatementPeriod(
        start=start, end=end, revenue=revenue, expenses=expenses, net_income_by_currency=net
    )


def build(
    session: Session,
    *,
    household_id: UUID,
    start: date_type,
    end: date_type,
    prior_start: date_type | None = None,
    prior_end: date_type | None = None,
    visible_account_filter: VisibleAccountFilter | None = None,
) -> IncomeStatementData:
    """Compute current-period (and optional prior-period) income statement."""
    from tulip_storage.models import Household

    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    current = _build_period(
        session,
        household_id=household_id,
        start=start,
        end=end,
        visible_account_filter=visible_account_filter,
    )
    prior: IncomeStatementPeriod | None = None
    if prior_start is not None and prior_end is not None:
        prior = _build_period(
            session,
            household_id=household_id,
            start=prior_start,
            end=prior_end,
            visible_account_filter=visible_account_filter,
        )
    return IncomeStatementData(
        current_period=current,
        prior_period=prior,
        household_name=household.name,
    )


def render_html(data: IncomeStatementData) -> str:
    """Render the income-statement data as HTML."""
    return get_renderer().render(
        "income_statement.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: IncomeStatementData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "income_statement.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_csv(data: IncomeStatementData) -> bytes:
    """Render income statement as CSV (P7.3): one row per (period, section, account)."""
    from tulip_reports.engine import ReportRenderer

    headers = ["Period", "Section", "Code", "Account", "Currency", "Amount"]
    rows: list[list[object]] = []
    for label, period in [("current", data.current_period)] + (
        [("prior", data.prior_period)] if data.prior_period else []
    ):
        for section in (period.revenue, period.expenses):
            for row in section.rows:
                rows.append(
                    [label, section.title, row.code or "", row.name, row.currency, row.amount]
                )
            for currency, subtotal in section.subtotals_by_currency.items():
                rows.append([label, section.title, "SUBTOTAL", "", currency, subtotal])
        for currency, net in period.net_income_by_currency.items():
            rows.append([label, "Net income", "", "", currency, net])
    return ReportRenderer.csv_bytes(headers, rows)


__all__ = [
    "IncomeStatementData",
    "IncomeStatementPeriod",
    "IncomeStatementRow",
    "IncomeStatementSection",
    "build",
    "render_csv",
    "render_html",
    "render_pdf",
]
