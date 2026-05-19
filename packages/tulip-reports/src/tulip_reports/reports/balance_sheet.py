"""Balance-sheet report (P7.1).

Point-in-time view of assets, liabilities, and equity. Derives from
the same per-account balance query as trial balance; the difference is
the grouping (by account type) and the equation that must hold:

    assets = liabilities + equity + retained earnings

For v1, retained earnings is approximated as the cumulative net of
income - expenses through the as_of date. Per ARCHITECTURE.md §1.1 the
ledger is single-currency-per-pool so the equation holds per currency.
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
class BalanceSheetRow:
    """One line in a balance-sheet section."""

    code: str | None
    name: str
    balance: Decimal
    currency: str
    #: Full ``Type:Name:...:Name`` path per #300. Templates render
    #: this; ``code`` + ``name`` stay on the dataclass for CSV.
    path: str = ""


@dataclass(frozen=True, slots=True)
class BalanceSheetSection:
    """Assets / Liabilities / Equity section + per-currency subtotals."""

    title: str
    rows: list[BalanceSheetRow]
    subtotals_by_currency: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class BalanceSheetData:
    """Everything the balance-sheet template needs to render."""

    as_of: date_type
    assets: BalanceSheetSection
    liabilities: BalanceSheetSection
    equity: BalanceSheetSection
    retained_earnings: dict[str, Decimal]  # cumulative income - expenses
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


VisibleAccountFilter = Callable[[str, UUID | None], bool]


def build(
    session: Session,
    *,
    household_id: UUID,
    as_of: date_type | None = None,
    visible_account_filter: VisibleAccountFilter | None = None,
) -> BalanceSheetData:
    """Compute balance-sheet sections grouped by account type."""
    from tulip_reports._account_path import account_path
    from tulip_storage.models import Household
    from tulip_storage.repositories import AccountRepository, TransactionRepository

    tx_repo = TransactionRepository(session, household_id)
    account_repo = AccountRepository(session, household_id)
    accounts_by_id = {a.id: a for a in account_repo.list_active()}
    effective_as_of = as_of or date_type.today()
    raw = tx_repo.trial_balance(as_of=effective_as_of)
    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    by_type: dict[str, list[BalanceSheetRow]] = {
        "asset": [],
        "liability": [],
        "equity": [],
    }
    retained: dict[str, Decimal] = {}

    for r in raw:
        a = accounts_by_id.get(r.account_id)
        if a is None:
            continue
        if visible_account_filter is not None and not visible_account_filter(
            a.visibility, a.created_by_user_id
        ):
            continue
        balance = Decimal(str(r.balance)).quantize(Decimal("0.01"))
        row = BalanceSheetRow(
            code=a.code,
            name=a.name,
            balance=balance,
            currency=r.currency,
            path=account_path(a.id, accounts_by_id),
        )
        type_value = a.type.value
        if type_value in ("asset", "liability", "equity"):
            by_type[type_value].append(row)
        elif type_value == "income":
            # Income increases retained earnings; ledger convention has
            # income as a negative balance (credit), so flip the sign.
            retained[r.currency] = retained.get(r.currency, Decimal("0")) - balance
        elif type_value == "expense":
            retained[r.currency] = retained.get(r.currency, Decimal("0")) - balance

    def _section(title: str, rows: list[BalanceSheetRow]) -> BalanceSheetSection:
        subtotals: dict[str, Decimal] = {}
        for row in rows:
            subtotals[row.currency] = subtotals.get(row.currency, Decimal("0")) + row.balance
        # Sort by code (then name) for stable rendering.
        rows_sorted = sorted(rows, key=lambda r: (r.code or "", r.name))
        return BalanceSheetSection(title=title, rows=rows_sorted, subtotals_by_currency=subtotals)

    return BalanceSheetData(
        as_of=effective_as_of,
        assets=_section("Assets", by_type["asset"]),
        liabilities=_section("Liabilities", by_type["liability"]),
        equity=_section("Equity", by_type["equity"]),
        retained_earnings={c: amt.quantize(Decimal("0.01")) for c, amt in retained.items()},
        household_name=household.name,
    )


def render_html(data: BalanceSheetData) -> str:
    """Render the balance-sheet data as HTML."""
    return get_renderer().render(
        "balance_sheet.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: BalanceSheetData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "balance_sheet.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_csv(data: BalanceSheetData) -> bytes:
    """Render balance sheet as CSV (P7.3): one row per (section, account)."""
    from tulip_reports.engine import ReportRenderer

    headers = ["Section", "Code", "Account", "Account Path", "Currency", "Balance"]
    rows: list[list[object]] = []
    for section in (data.assets, data.liabilities, data.equity):
        for row in section.rows:
            rows.append(
                [section.title, row.code or "", row.name, row.path, row.currency, row.balance]
            )
        for currency, subtotal in section.subtotals_by_currency.items():
            rows.append([section.title, "SUBTOTAL", "", "", currency, subtotal])
    for currency, amount in data.retained_earnings.items():
        rows.append(["Retained earnings", "", "", "", currency, amount])
    return ReportRenderer.csv_bytes(headers, rows)


__all__ = [
    "BalanceSheetData",
    "BalanceSheetRow",
    "BalanceSheetSection",
    "build",
    "render_csv",
    "render_html",
    "render_pdf",
]
