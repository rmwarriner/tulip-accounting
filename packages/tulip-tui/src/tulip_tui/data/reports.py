"""Reports browser data adapter.

The catalogue (`REPORT_CATALOGUE`) is the v1 list of TUI-browseable
reports — the eight ``/v1/reports/*`` endpoints with sensible default
params for an interactive browse session. ``custom-query`` is omitted
because it requires SQL the user has to type out; surfacing that as a
TUI surface is a later slice.

Date defaults for period-based reports use the current calendar
month — interactive browsing is "show me what's happening *now*",
and the user can drill deeper via the CLI when they need a different
window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from tulip_cli.http import TulipClient


def _month_bounds() -> dict[str, str]:
    today = datetime.now(UTC).date()
    start = today.replace(day=1)
    # First day of next month - one day → last day of this month.
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = date.fromordinal(next_month.toordinal() - 1)
    return {"start": start.isoformat(), "end": end.isoformat()}


@dataclass(frozen=True, slots=True)
class ReportSpec:
    """One entry in the reports catalogue."""

    key: str
    title: str
    endpoint: str
    default_params_factory: Callable[[], dict[str, str]] = field(default=dict)


@dataclass(frozen=True, slots=True)
class ReportPayload:
    """A fetched report — the spec it came from plus the parsed JSON body."""

    spec: ReportSpec
    body: dict[str, Any]


REPORT_CATALOGUE: tuple[ReportSpec, ...] = (
    ReportSpec(
        key="trial-balance",
        title="Trial balance",
        endpoint="/v1/reports/trial-balance",
    ),
    ReportSpec(
        key="balance-sheet",
        title="Balance sheet",
        endpoint="/v1/reports/balance-sheet",
    ),
    ReportSpec(
        key="income-statement",
        title="Income statement (this month)",
        endpoint="/v1/reports/income-statement",
        default_params_factory=_month_bounds,
    ),
    ReportSpec(
        key="cash-flow",
        title="Cash flow (this month)",
        endpoint="/v1/reports/cash-flow",
        default_params_factory=_month_bounds,
    ),
    ReportSpec(
        key="envelope-status",
        title="Envelope status",
        endpoint="/v1/reports/envelope-status",
    ),
    ReportSpec(
        key="sinking-fund-progress",
        title="Sinking fund progress",
        endpoint="/v1/reports/sinking-fund-progress",
    ),
    ReportSpec(
        key="reconciliation-summary",
        title="Reconciliation summary",
        endpoint="/v1/reports/reconciliation-summary",
    ),
    ReportSpec(
        key="audit-log",
        title="Audit log (most recent)",
        endpoint="/v1/reports/audit-log",
    ),
)


def load_report(client: TulipClient, spec: ReportSpec) -> ReportPayload:
    """Fetch ``spec`` from the API and return the parsed JSON body."""
    params = dict(spec.default_params_factory())
    params.setdefault("format", "json")
    response = client.get(spec.endpoint, authenticated=True, params=params)
    body = response.json()
    if not isinstance(body, dict):
        # The API contract is "report responses are JSON objects." A
        # non-object body would be a contract violation — wrap it so
        # the screen can render *something* rather than crash.
        body = {"value": body}
    return ReportPayload(spec=spec, body=body)


__all__: list[str] = [
    "REPORT_CATALOGUE",
    "ReportPayload",
    "ReportSpec",
    "load_report",
]
