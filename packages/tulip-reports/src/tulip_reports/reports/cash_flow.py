"""Cash-flow report (P7.1).

Net change in asset accounts over a date range, classified by direction
(inflows vs outflows). For v1 this is a simple sign-based split — money
moving into asset accounts (positive delta) is an inflow, money out
(negative delta) is an outflow. The operating / investing / financing
classification used in formal GAAP cash flows is out of scope for v1.
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
class CashFlowRow:
    """One asset account's net change over the period."""

    code: str | None
    name: str
    delta: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CashFlowData:
    """Everything the cash-flow template needs to render."""

    start: date_type
    end: date_type
    inflows: list[CashFlowRow]
    outflows: list[CashFlowRow]
    net_by_currency: dict[str, Decimal]
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


VisibleAccountFilter = Callable[[str, UUID | None], bool]


def build(
    session: Session,
    *,
    household_id: UUID,
    start: date_type,
    end: date_type,
    visible_account_filter: VisibleAccountFilter | None = None,
) -> CashFlowData:
    """Compute per-asset-account net change between ``start`` and ``end``."""
    from tulip_storage.models import Household
    from tulip_storage.repositories import AccountRepository, TransactionRepository

    tx_repo = TransactionRepository(session, household_id)
    account_repo = AccountRepository(session, household_id)
    accounts_by_id = {a.id: a for a in account_repo.list_active()}
    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    end_rows = {
        (r.account_id, r.currency): Decimal(str(r.balance))
        for r in tx_repo.trial_balance(as_of=end)
    }
    prior_day = date_type.fromordinal(max(1, start.toordinal() - 1))
    start_rows = {
        (r.account_id, r.currency): Decimal(str(r.balance))
        for r in tx_repo.trial_balance(as_of=prior_day)
    }

    inflows: list[CashFlowRow] = []
    outflows: list[CashFlowRow] = []
    net: dict[str, Decimal] = {}

    for (account_id, currency), end_bal in end_rows.items():
        a = accounts_by_id.get(account_id)
        if a is None or a.type.value != "asset":
            continue
        if visible_account_filter is not None and not visible_account_filter(
            a.visibility, a.created_by_user_id
        ):
            continue
        delta = (end_bal - start_rows.get((account_id, currency), Decimal("0"))).quantize(
            Decimal("0.01")
        )
        if delta == 0:
            continue
        row = CashFlowRow(code=a.code, name=a.name, delta=delta, currency=currency)
        if delta > 0:
            inflows.append(row)
        else:
            outflows.append(row)
        net[currency] = net.get(currency, Decimal("0")) + delta

    return CashFlowData(
        start=start,
        end=end,
        inflows=sorted(inflows, key=lambda r: (r.code or "", r.name)),
        outflows=sorted(outflows, key=lambda r: (r.code or "", r.name)),
        net_by_currency={c: amt.quantize(Decimal("0.01")) for c, amt in net.items()},
        household_name=household.name,
    )


def render_html(data: CashFlowData) -> str:
    """Render the cash-flow data as HTML."""
    return get_renderer().render(
        "cash_flow.html",
        data=data,
        generated_at=data.generated_at,
    )


__all__ = ["CashFlowData", "CashFlowRow", "build", "render_html"]
