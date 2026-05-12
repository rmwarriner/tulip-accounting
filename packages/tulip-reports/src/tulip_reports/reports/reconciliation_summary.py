"""Reconciliation summary report (P7.1).

Aggregate view of completed and in-progress reconciliations. Each row
shows the per-account status with statement period, ending balance,
and match counts. Helpful for "what's been reconciled lately" review.
"""

from __future__ import annotations

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
class ReconciliationRow:
    """One reconciliation's summary row."""

    reconciliation_id: UUID
    account_name: str
    account_code: str | None
    period_start: date_type
    period_end: date_type
    starting_balance: Decimal
    ending_balance: Decimal
    currency: str
    status: str
    match_count: int
    carry_forward_count: int


@dataclass(frozen=True, slots=True)
class ReconciliationSummaryData:
    """Everything the reconciliation-summary template needs to render."""

    rows: list[ReconciliationRow]
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build(
    session: Session,
    *,
    household_id: UUID,
    status_filter: str | None = None,
) -> ReconciliationSummaryData:
    """List reconciliations newest-first with match + carry-forward counts."""
    from sqlalchemy import func, select

    from tulip_storage.models import (
        Account,
        Household,
        Reconciliation,
        ReconciliationMatch,
        Transaction,
    )

    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    query = (
        select(Reconciliation, Account)
        .join(
            Account,
            (Account.household_id == Reconciliation.household_id)
            & (Account.id == Reconciliation.account_id),
        )
        .where(Reconciliation.household_id == household_id)
        .order_by(Reconciliation.statement_period_end.desc(), Reconciliation.id.desc())
    )
    if status_filter:
        query = query.where(Reconciliation.status == status_filter)

    rows: list[ReconciliationRow] = []
    for recon, account in session.execute(query).all():
        match_count = (
            session.execute(
                select(func.count())
                .select_from(ReconciliationMatch)
                .where(
                    ReconciliationMatch.household_id == household_id,
                    ReconciliationMatch.reconciliation_id == recon.id,
                )
            ).scalar_one()
            or 0
        )
        carry_count = (
            session.execute(
                select(func.count())
                .select_from(Transaction)
                .where(
                    Transaction.household_id == household_id,
                    Transaction.carried_forward_from_reconciliation_id == recon.id,
                )
            ).scalar_one()
            or 0
        )
        rows.append(
            ReconciliationRow(
                reconciliation_id=recon.id,
                account_name=account.name,
                account_code=account.code,
                period_start=recon.statement_period_start,
                period_end=recon.statement_period_end,
                starting_balance=Decimal(str(recon.starting_balance)).quantize(Decimal("0.01")),
                ending_balance=Decimal(str(recon.ending_balance)).quantize(Decimal("0.01")),
                currency=recon.currency,
                status=recon.status,
                match_count=int(match_count),
                carry_forward_count=int(carry_count),
            )
        )

    return ReconciliationSummaryData(rows=rows, household_name=household.name)


def render_html(data: ReconciliationSummaryData) -> str:
    """Render the reconciliation-summary data as HTML."""
    return get_renderer().render(
        "reconciliation_summary.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: ReconciliationSummaryData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "reconciliation_summary.html",
        data=data,
        generated_at=data.generated_at,
    )


__all__ = [
    "ReconciliationRow",
    "ReconciliationSummaryData",
    "build",
    "render_html",
    "render_pdf",
]
