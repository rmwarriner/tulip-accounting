"""Sinking-fund progress report (P7.1).

Active sinking funds with current balance vs target amount + target
date. Progress is computed as ``balance / target_amount`` and rendered
as a percentage; days-to-target gives a sense of time pressure.
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
class SinkingFundRow:
    """One sinking fund's progress snapshot."""

    fund_id: UUID
    name: str
    currency: str
    balance: Decimal
    target_amount: Decimal
    target_date: date_type
    progress_pct: Decimal
    days_remaining: int
    remaining_to_target: Decimal


@dataclass(frozen=True, slots=True)
class SinkingFundProgressData:
    """Everything the sinking-fund-progress template needs to render."""

    as_of: date_type
    rows: list[SinkingFundRow]
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build(
    session: Session,
    *,
    household_id: UUID,
    as_of: date_type | None = None,
) -> SinkingFundProgressData:
    """List active sinking funds with balance + target snapshot."""
    from sqlalchemy import select

    from tulip_storage.models import AllocationPool, Household, PoolType, SinkingFund
    from tulip_storage.repositories import ShadowTransactionRepository

    effective_as_of = as_of or date_type.today()
    shadow_repo = ShadowTransactionRepository(session, household_id)
    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    pools = session.execute(
        select(AllocationPool, SinkingFund)
        .join(
            SinkingFund,
            (SinkingFund.household_id == AllocationPool.household_id)
            & (SinkingFund.pool_id == AllocationPool.id),
        )
        .where(
            AllocationPool.household_id == household_id,
            AllocationPool.pool_type == PoolType.SINKING_FUND,
            AllocationPool.is_active.is_(True),
        )
    ).all()
    pool_ids = [p.id for p, _ in pools]
    balances = shadow_repo.balances_for_pools(pool_ids, as_of=effective_as_of)

    rows: list[SinkingFundRow] = []
    for pool, fund in pools:
        balance = (
            balances.get(pool.id, {}).get(pool.currency, Decimal("0")).quantize(Decimal("0.01"))
        )
        target = Decimal(fund.target_amount).quantize(Decimal("0.01"))
        progress = (
            (balance / target * 100).quantize(Decimal("0.1")) if target != 0 else Decimal("0")
        )
        remaining = (target - balance).quantize(Decimal("0.01"))
        days = (fund.target_date - effective_as_of).days
        rows.append(
            SinkingFundRow(
                fund_id=pool.id,
                name=pool.name,
                currency=pool.currency,
                balance=balance,
                target_amount=target,
                target_date=fund.target_date,
                progress_pct=progress,
                days_remaining=days,
                remaining_to_target=remaining,
            )
        )

    return SinkingFundProgressData(
        as_of=effective_as_of,
        rows=sorted(rows, key=lambda r: r.target_date),
        household_name=household.name,
    )


def render_html(data: SinkingFundProgressData) -> str:
    """Render the sinking-fund-progress data as HTML."""
    return get_renderer().render(
        "sinking_fund_progress.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: SinkingFundProgressData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "sinking_fund_progress.html",
        data=data,
        generated_at=data.generated_at,
    )


__all__ = [
    "SinkingFundProgressData",
    "SinkingFundRow",
    "build",
    "render_html",
    "render_pdf",
]
