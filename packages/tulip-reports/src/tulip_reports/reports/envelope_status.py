"""Envelope-status report (P7.1).

Active envelopes with current balance vs budget for the current
period. Reuses the existing shadow-balance query (the same one
``GET /v1/envelopes`` uses for its inline balance column).
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
class EnvelopeStatusRow:
    """One envelope's current snapshot."""

    envelope_id: UUID
    name: str
    currency: str
    balance: Decimal
    budget_amount: Decimal | None
    budget_period: str  # weekly / biweekly / monthly / quarterly / yearly
    rollover_policy: str  # reset / accumulate
    utilization_pct: Decimal | None  # spend / budget; None if no budget set


@dataclass(frozen=True, slots=True)
class EnvelopeStatusData:
    """Everything the envelope-status template needs to render."""

    as_of: date_type
    rows: list[EnvelopeStatusRow]
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build(
    session: Session,
    *,
    household_id: UUID,
    as_of: date_type | None = None,
) -> EnvelopeStatusData:
    """List active envelopes with current balance + budget snapshot."""
    from sqlalchemy import select

    from tulip_storage.models import AllocationPool, Envelope, Household, PoolType
    from tulip_storage.repositories import ShadowTransactionRepository

    effective_as_of = as_of or date_type.today()
    shadow_repo = ShadowTransactionRepository(session, household_id)
    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    pools = session.execute(
        select(AllocationPool, Envelope)
        .join(
            Envelope,
            (Envelope.household_id == AllocationPool.household_id)
            & (Envelope.pool_id == AllocationPool.id),
        )
        .where(
            AllocationPool.household_id == household_id,
            AllocationPool.pool_type == PoolType.ENVELOPE,
            AllocationPool.is_active.is_(True),
        )
    ).all()
    pool_ids = [p.id for p, _ in pools]
    balances = shadow_repo.balances_for_pools(pool_ids, as_of=effective_as_of)

    rows: list[EnvelopeStatusRow] = []
    for pool, env in pools:
        balance = (
            balances.get(pool.id, {}).get(pool.currency, Decimal("0")).quantize(Decimal("0.01"))
        )
        utilization: Decimal | None
        if env.budget_amount is not None and env.budget_amount != 0:
            spend = max(env.budget_amount - balance, Decimal("0"))
            utilization = (spend / env.budget_amount * 100).quantize(Decimal("0.1"))
        else:
            utilization = None
        rows.append(
            EnvelopeStatusRow(
                envelope_id=pool.id,
                name=pool.name,
                currency=pool.currency,
                balance=balance,
                budget_amount=(
                    env.budget_amount.quantize(Decimal("0.01")) if env.budget_amount else None
                ),
                budget_period=env.budget_period.value,
                rollover_policy=env.rollover_policy.value,
                utilization_pct=utilization,
            )
        )

    return EnvelopeStatusData(
        as_of=effective_as_of,
        rows=sorted(rows, key=lambda r: r.name),
        household_name=household.name,
    )


def render_html(data: EnvelopeStatusData) -> str:
    """Render the envelope-status data as HTML."""
    return get_renderer().render(
        "envelope_status.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: EnvelopeStatusData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "envelope_status.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_csv(data: EnvelopeStatusData) -> bytes:
    """Render envelope status as CSV (P7.3): one row per envelope."""
    from tulip_reports.engine import ReportRenderer

    headers = [
        "Envelope id",
        "Name",
        "Currency",
        "Balance",
        "Budget",
        "Budget period",
        "Rollover",
        "Utilization %",
    ]
    rows: list[list[object]] = [
        [
            row.envelope_id,
            row.name,
            row.currency,
            row.balance,
            row.budget_amount if row.budget_amount is not None else "",
            row.budget_period,
            row.rollover_policy,
            row.utilization_pct if row.utilization_pct is not None else "",
        ]
        for row in data.rows
    ]
    return ReportRenderer.csv_bytes(headers, rows)


__all__ = [
    "EnvelopeStatusData",
    "EnvelopeStatusRow",
    "build",
    "render_csv",
    "render_html",
    "render_pdf",
]
