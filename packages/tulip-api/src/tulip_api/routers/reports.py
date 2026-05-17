"""GET /v1/reports/trial-balance (P7.1: + HTML rendering).

Trial balance is the canonical "is the ledger healthy" view: every
posted transaction's debit and credit postings should sum to zero per
currency. The endpoint exposes both the per-account rows and the
per-currency totals so a caller can both display the report and assert
the zero-sum invariant.

P7.1 adds ``?format=html`` to render via ``tulip_reports``; default
remains JSON for backward compatibility.

Pending transactions are excluded by default (they're workflow state,
not ledger state); ``?include_pending=true`` (#274) folds them in.
Role-based filtering matches ``GET /v1/accounts`` — admins see private
accounts, members only see shared accounts and their own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import TulipProblem, problem_response
from tulip_api.schemas.balance import (
    CurrencyTotal,
    TrialBalanceRead,
    TrialBalanceRow,
)
from tulip_core.money import Money
from tulip_storage.repositories import AccountRepository, TransactionRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/reports", tags=["reports"])


def _filter_for_role(account_visibility: str, created_by: object, claims: Claims) -> bool:
    """Mirror routers.accounts._filter_for_role for trial-balance row filtering."""
    if account_visibility == "shared":
        return True
    if claims.role == "admin":
        return True
    return created_by == claims.user_id


@router.get(
    "/trial-balance",
    response_model=None,  # response can be JSON or HTML; per-format handler below
    responses={
        200: {
            "description": "Trial-balance report (JSON by default; HTML when format=html).",
        },
        401: problem_response("auth.unauthorized"),
    },
)
def trial_balance(
    as_of: date_type | None = Query(  # noqa: B008 — FastAPI uses Query() in defaults
        default=None,
        description=(
            "Optional point-in-time date (YYYY-MM-DD). Includes only "
            "transactions on or before this date. Defaults to today."
        ),
    ),
    format: Literal["json", "html", "pdf", "csv"] = Query(
        default="json",
        description=(
            "Response format. ``json`` (default) returns the structured "
            "shape for programmatic use; ``html`` returns a toner-friendly "
            "rendered HTML document for screen review or printing."
        ),
    ),
    include_pending: bool = Query(
        default=False,
        description=(
            "When true, fold PENDING transactions into the report — the "
            "'what if all pending is real' view. Default false keeps the "
            "posted-only ledger semantics. The response then carries "
            "pending_included=true, pending_count, and has_pending on each "
            "row that drew a PENDING posting."
        ),
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Return per-account, per-currency balances for the household ledger.

    By default only POSTED + RECONCILED contribute; ``include_pending=true``
    (#274) folds PENDING transactions in. Visibility filtering matches the
    accounts list — accounts the caller can't see don't appear here.
    """
    effective_as_of = as_of or date_type.today()
    tx_repo = TransactionRepository(session, claims.household_id)
    account_repo = AccountRepository(session, claims.household_id)

    accounts_by_id = {a.id: a for a in account_repo.list_active()}
    raw = tx_repo.trial_balance(as_of=effective_as_of, include_pending=include_pending)
    pending_count = (
        tx_repo.count_pending_transactions(as_of=effective_as_of) if include_pending else 0
    )

    rows: list[TrialBalanceRow] = []
    debits_by_currency: dict[str, Decimal] = {}
    credits_by_currency: dict[str, Decimal] = {}
    for r in raw:
        a = accounts_by_id.get(r.account_id)
        if a is None or not _filter_for_role(a.visibility, a.created_by_user_id, claims):
            continue
        balance = Money(r.balance, r.currency).quantize_to_currency().amount
        rows.append(
            TrialBalanceRow(
                account_id=a.id,
                code=a.code,
                name=a.name,
                type=a.type.value,
                currency=r.currency,
                balance=balance,
                has_pending=r.has_pending,
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
            debits=Money(debits_by_currency.get(c, Decimal("0")), c).quantize_to_currency().amount,
            credits=Money(credits_by_currency.get(c, Decimal("0")), c)
            .quantize_to_currency()
            .amount,
        )
        for c in currencies
    ]

    json_body = TrialBalanceRead(
        as_of=effective_as_of,
        rows=rows,
        totals_by_currency=totals,
        pending_included=include_pending,
        pending_count=pending_count,
    )
    if format in ("html", "pdf", "csv"):
        from tulip_reports.reports import trial_balance as report_module

        data = report_module.build(
            session,
            household_id=claims.household_id,
            as_of=effective_as_of,
            visible_account_filter=lambda vis, by: _filter_for_role(vis, by, claims),
            include_pending=include_pending,
        )
        return _report_response(
            data,
            report_module.render_html,
            format,
            render_pdf=report_module.render_pdf,
            render_csv=report_module.render_csv,
            pdf_filename=f"trial-balance-{effective_as_of.isoformat()}.pdf",
            csv_filename=f"trial-balance-{effective_as_of.isoformat()}.csv",
        )
    return Response(
        content=json_body.model_dump_json(),
        media_type="application/json",
    )


# -------------------------------------------------------------------------
# P7.1: 8 additional reports
#
# Each endpoint accepts ``?format=json|html`` (default json). JSON shape is
# derived from the report's dataclass via ``_to_jsonable`` below, which
# stringifies Decimal / UUID / date / datetime so the default FastAPI
# encoder can handle them. The HTML branch calls the report's render_html.
# -------------------------------------------------------------------------


def _to_jsonable(obj: object) -> object:
    """Convert dataclasses / Decimals / UUIDs / dates to JSON-serializable forms."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (UUID, datetime)):
        return obj.isoformat() if isinstance(obj, datetime) else str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


#: Security headers applied to every HTML report response. Defense-in-
#: depth against a hypothetical template-escaping bug (security audit
#: L-22). Tulip's report templates pull no remote resources and use only
#: inline ``<style>`` (no inline script), so the policy below is the
#: tightest one that still renders correctly. Adjust ``style-src`` if a
#: report ever needs an external stylesheet.
_HTML_REPORT_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _report_response(
    data: object,
    render_html: Callable[..., str],
    format: str,
    *,
    render_pdf: Callable[..., bytes] | None = None,
    render_csv: Callable[..., bytes] | None = None,
    pdf_filename: str = "report.pdf",
    csv_filename: str = "report.csv",
) -> Response:
    """Common JSON / HTML / PDF / CSV branch used by the report endpoints."""
    if format == "html":
        return HTMLResponse(
            content=render_html(data),
            headers=_HTML_REPORT_SECURITY_HEADERS,
        )
    if format == "pdf":
        if render_pdf is None:

            class _PdfUnavailable(TulipProblem):
                def __init__(self) -> None:
                    super().__init__(
                        code="report.pdf_not_supported",
                        title="PDF not supported for this report",
                        status=400,
                        detail="This report doesn't have a PDF renderer wired up.",
                    )

            raise _PdfUnavailable()
        return Response(
            content=render_pdf(data),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{pdf_filename}"'},
        )
    if format == "csv":
        if render_csv is None:

            class _CsvUnavailable(TulipProblem):
                def __init__(self) -> None:
                    super().__init__(
                        code="report.csv_not_supported",
                        title="CSV not supported for this report",
                        status=400,
                        detail="This report doesn't have a CSV renderer wired up.",
                    )

            raise _CsvUnavailable()
        return Response(
            content=render_csv(data),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{csv_filename}"'},
        )
    return Response(content=json.dumps(_to_jsonable(data)), media_type="application/json")


@router.get(
    "/balance-sheet",
    response_model=None,
    responses={
        200: {"description": "Balance sheet (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def balance_sheet(
    as_of: date | None = Query(default=None),  # noqa: B008
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Balance sheet at ``as_of`` (point-in-time). HTML via tulip_reports (P7.1)."""
    from tulip_reports.reports import balance_sheet as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        as_of=as_of,
        visible_account_filter=lambda vis, by: _filter_for_role(vis, by, claims),
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="trial-balance.pdf",
        render_csv=report_module.render_csv,
        csv_filename="trial-balance.csv",
    )


@router.get(
    "/income-statement",
    response_model=None,
    responses={
        200: {"description": "Income statement (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def income_statement(
    start: date = Query(...),  # noqa: B008
    end: date = Query(...),  # noqa: B008
    prior_start: date | None = Query(default=None),  # noqa: B008
    prior_end: date | None = Query(default=None),  # noqa: B008
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Income statement over ``start``→``end`` with optional comparison period."""
    from tulip_reports.reports import income_statement as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        start=start,
        end=end,
        prior_start=prior_start,
        prior_end=prior_end,
        visible_account_filter=lambda vis, by: _filter_for_role(vis, by, claims),
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="income-statement.pdf",
        render_csv=report_module.render_csv,
        csv_filename="income-statement.csv",
    )


@router.get(
    "/cash-flow",
    response_model=None,
    responses={
        200: {"description": "Cash flow (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def cash_flow(
    start: date = Query(...),  # noqa: B008
    end: date = Query(...),  # noqa: B008
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Cash flow (net change per asset account) over ``start``→``end``."""
    from tulip_reports.reports import cash_flow as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        start=start,
        end=end,
        visible_account_filter=lambda vis, by: _filter_for_role(vis, by, claims),
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="cash-flow.pdf",
        render_csv=report_module.render_csv,
        csv_filename="cash-flow.csv",
    )


@router.get(
    "/envelope-status",
    response_model=None,
    responses={
        200: {"description": "Envelope status (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def envelope_status(
    as_of: date | None = Query(default=None),  # noqa: B008
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Active envelopes with current balance + budget snapshot at ``as_of``."""
    from tulip_reports.reports import envelope_status as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        as_of=as_of,
        visible_pool_filter=lambda vis, by: _filter_for_role(vis, by, claims),
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="envelope-status.pdf",
        render_csv=report_module.render_csv,
        csv_filename="envelope-status.csv",
    )


@router.get(
    "/sinking-fund-progress",
    response_model=None,
    responses={
        200: {"description": "Sinking-fund progress (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def sinking_fund_progress(
    as_of: date | None = Query(default=None),  # noqa: B008
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Active sinking funds with balance vs target snapshot at ``as_of``."""
    from tulip_reports.reports import sinking_fund_progress as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        as_of=as_of,
        visible_pool_filter=lambda vis, by: _filter_for_role(vis, by, claims),
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="sinking-fund-progress.pdf",
        render_csv=report_module.render_csv,
        csv_filename="sinking-fund-progress.csv",
    )


@router.get(
    "/reconciliation-summary",
    response_model=None,
    responses={
        200: {"description": "Reconciliation summary (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def reconciliation_summary(
    status_filter: str | None = Query(default=None, alias="status"),
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Reconciliations newest-first with optional status filter."""
    from tulip_reports.reports import reconciliation_summary as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        status_filter=status_filter,
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="reconciliation-summary.pdf",
        render_csv=report_module.render_csv,
        csv_filename="reconciliation-summary.csv",
    )


@router.get(
    "/audit-log",
    response_model=None,
    responses={
        200: {"description": "Audit-log report (JSON or HTML)."},
        401: problem_response("auth.unauthorized"),
    },
)
def audit_log(
    start: date | None = Query(default=None),  # noqa: B008
    end: date | None = Query(default=None),  # noqa: B008
    actor_user_id: UUID | None = Query(default=None),  # noqa: B008
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(require_role("admin")),  # noqa: B008 — #229 admin-only
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Filtered, paginated audit-log report (**admin-only** per #229).

    Audit rows carry user-typed `description`, `reference`, and `before/after`
    JSON snapshots — exposing them to non-admin household members would
    let any member study another member's activity at high fidelity.
    Gating on admin matches the Phase-7 docs' implicit intent.
    """
    from tulip_reports.reports import audit_log as report_module

    data = report_module.build(
        session,
        household_id=claims.household_id,
        start=start,
        end=end,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="audit-log.pdf",
        render_csv=report_module.render_csv,
        csv_filename="audit-log.csv",
    )


class CustomQueryUnsafeError(TulipProblem):
    """Custom-query SQL was rejected by ``tulip_ai.sql_safety`` (P7.1).

    Raised when the requested SQL writes, reads a non-allowlisted
    table, or otherwise fails the safety pass that backs the AI NL-
    query capability. Same gate, same wording — the custom-query
    report and the NL-query capability share security guarantees.
    """

    def __init__(self, reason: str) -> None:
        """Build the ``report.unsafe_query`` problem."""
        super().__init__(
            code="report.unsafe_query",
            title="Custom query rejected by SQL safety check",
            status=400,
            detail=reason,
        )


@router.get(
    "/custom-query",
    response_model=None,
    responses={
        200: {"description": "Custom-query report (JSON or HTML)."},
        400: problem_response("report.unsafe_query"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def custom_query(
    sql: str = Query(..., description="Read-only SELECT against AI views (P6.2)."),
    format: Literal["json", "html", "pdf", "csv"] = Query(default="json"),
    claims: Claims = Depends(require_role("admin")),  # noqa: B008 — #349 / audit L-7
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Run a read-only SELECT against the AI views; render as a table.

    Queries are validated by ``tulip_ai.sql_safety.validate_and_rewrite``
    — writes, non-AI-view reads, and joins outside the allowlist raise
    ``UnsafeSQLError`` which we surface as a 400 Problem Details.

    Admin-only per security audit L-7 (#349). The SQL-safety pass
    constrains *which* SQL runs, not which tenant rows are visible —
    a member running custom-query would observe private-pool data the
    visibility filter would otherwise hide. Restricting to admin
    sidesteps the cross-user-within-household concern; the broader
    visibility-filter bundle stays gated on multi-user invite (#237).
    """
    from tulip_ai.sql_safety import UnsafeSQLError
    from tulip_reports.reports import custom_query as report_module

    try:
        data = report_module.build(session, household_id=claims.household_id, sql=sql)
    except UnsafeSQLError as exc:
        raise CustomQueryUnsafeError(str(exc)) from exc
    return _report_response(
        data,
        report_module.render_html,
        format,
        render_pdf=report_module.render_pdf,
        pdf_filename="custom-query.pdf",
        render_csv=report_module.render_csv,
        csv_filename="custom-query.csv",
    )
