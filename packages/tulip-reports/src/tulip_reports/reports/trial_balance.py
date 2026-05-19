"""Trial-balance report (P7.1).

The existing ``GET /v1/reports/trial-balance`` endpoint already
computes per-account, per-currency balances + the per-currency
debit/credit totals. This module re-uses the same storage queries and
adds an HTML renderer.

Per ARCHITECTURE.md §4.2 the trial balance is the canonical
"is the ledger healthy" view: every posted transaction's debit and
credit postings sum to zero per currency, so the totals row should
show equal debit and credit totals per currency.
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
class TrialBalanceRow:
    """One row of the trial-balance table."""

    account_id: UUID
    code: str | None
    name: str
    type: str  # asset / liability / equity / income / expense
    currency: str
    balance: Decimal
    has_pending: bool = False
    #: Full ``Type:Name:...:Name`` path per #300. ``code`` and ``name``
    #: are kept for back-compat in CSV output (which adds ``path`` as
    #: a new column rather than replacing the existing ones).
    path: str = ""


@dataclass(frozen=True, slots=True)
class CurrencyTotal:
    """Per-currency debit + credit totals."""

    currency: str
    debits: Decimal
    credits: Decimal


@dataclass(frozen=True, slots=True)
class TrialBalanceData:
    """Everything the trial-balance template needs to render."""

    as_of: date_type
    rows: list[TrialBalanceRow]
    totals_by_currency: list[CurrencyTotal]
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pending_included: bool = False
    pending_count: int = 0


VisibleAccountFilter = Callable[[str, UUID | None], bool]


def build(
    session: Session,
    *,
    household_id: UUID,
    as_of: date_type | None = None,
    visible_account_filter: VisibleAccountFilter | None = None,
    include_pending: bool = False,
) -> TrialBalanceData:
    """Compute trial-balance rows + totals for one household.

    ``visible_account_filter`` is an optional callable
    ``(visibility, created_by_user_id) -> bool`` used by the API layer
    to enforce role-based account visibility (admins see private
    accounts; members + viewers see shared accounts and their own).
    Defaults to a permissive filter that returns all accounts; tests
    use the default, the API endpoint passes its
    :func:`_filter_for_role`.

    ``include_pending`` (#274) folds PENDING transactions into the
    balances and stamps ``has_pending`` on each row that drew one; the
    rendered report shows a "includes N pending transactions" subtitle.
    """
    from tulip_reports._account_path import account_path
    from tulip_storage.models import Household
    from tulip_storage.repositories import AccountRepository, TransactionRepository

    tx_repo = TransactionRepository(session, household_id)
    account_repo = AccountRepository(session, household_id)
    accounts_by_id = {a.id: a for a in account_repo.list_active()}
    effective_as_of = as_of or date_type.today()
    raw = tx_repo.trial_balance(as_of=effective_as_of, include_pending=include_pending)
    pending_count = (
        tx_repo.count_pending_transactions(as_of=effective_as_of) if include_pending else 0
    )
    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101 — caller already authenticated

    rows: list[TrialBalanceRow] = []
    debits_by_currency: dict[str, Decimal] = {}
    credits_by_currency: dict[str, Decimal] = {}
    for r in raw:
        a = accounts_by_id.get(r.account_id)
        if a is None:
            continue
        if visible_account_filter is not None and not visible_account_filter(
            a.visibility, a.created_by_user_id
        ):
            continue
        balance = Decimal(str(r.balance)).quantize(Decimal("0.01"))
        rows.append(
            TrialBalanceRow(
                account_id=a.id,
                code=a.code,
                name=a.name,
                type=a.type.value,
                currency=r.currency,
                balance=balance,
                has_pending=r.has_pending,
                path=account_path(a.id, accounts_by_id),
            )
        )
        if balance > 0:
            debits_by_currency[r.currency] = (
                debits_by_currency.get(r.currency, Decimal("0")) + balance
            )
        elif balance < 0:
            credits_by_currency[r.currency] = credits_by_currency.get(r.currency, Decimal("0")) + (
                -balance
            )

    currencies = sorted(set(debits_by_currency) | set(credits_by_currency))
    totals = [
        CurrencyTotal(
            currency=c,
            debits=debits_by_currency.get(c, Decimal("0")).quantize(Decimal("0.01")),
            credits=credits_by_currency.get(c, Decimal("0")).quantize(Decimal("0.01")),
        )
        for c in currencies
    ]

    return TrialBalanceData(
        as_of=effective_as_of,
        rows=rows,
        totals_by_currency=totals,
        household_name=household.name,
        pending_included=include_pending,
        pending_count=pending_count,
    )


def render_html(data: TrialBalanceData) -> str:
    """Render the trial-balance data as HTML."""
    return get_renderer().render(
        "trial_balance.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: TrialBalanceData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "trial_balance.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_csv(data: TrialBalanceData) -> bytes:
    """Render the report as CSV bytes (P7.3).

    One row per (account, currency); per-currency totals at the end
    with a sentinel ``Code`` value of ``TOTAL`` so consumers can
    distinguish data rows from summary rows.
    """
    from tulip_reports.engine import ReportRenderer

    headers = ["Code", "Account", "Account Path", "Type", "Currency", "Balance"]
    rows: list[list[object]] = [
        [row.code or "", row.name, row.path, row.type, row.currency, row.balance]
        for row in data.rows
    ]
    for total in data.totals_by_currency:
        rows.append(
            [
                "TOTAL",
                f"Debits / Credits in {total.currency}",
                "",
                "",
                total.currency,
                f"DR {total.debits} / CR {total.credits}",
            ]
        )
    return ReportRenderer.csv_bytes(headers, rows)


__all__ = [
    "CurrencyTotal",
    "TrialBalanceData",
    "TrialBalanceRow",
    "build",
    "render_csv",
    "render_html",
    "render_pdf",
]
