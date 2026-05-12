"""GET /v1/reports/trial-balance (P7.1: + HTML rendering).

Trial balance is the canonical "is the ledger healthy" view: every
posted transaction's debit and credit postings should sum to zero per
currency. The endpoint exposes both the per-account rows and the
per-currency totals so a caller can both display the report and assert
the zero-sum invariant.

P7.1 adds ``?format=html`` to render via ``tulip_reports``; default
remains JSON for backward compatibility.

Pending transactions are excluded (they're workflow state, not ledger
state). Role-based filtering matches ``GET /v1/accounts`` — admins see
private accounts, members and viewers only see shared accounts and
their own.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response

from tulip_api.auth.deps import get_current_claims
from tulip_api.deps import get_session
from tulip_api.errors import problem_response
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
    format: Literal["json", "html"] = Query(
        default="json",
        description=(
            "Response format. ``json`` (default) returns the structured "
            "shape for programmatic use; ``html`` returns a toner-friendly "
            "rendered HTML document for screen review or printing."
        ),
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Return per-account, per-currency balances for the household ledger.

    Pending transactions are excluded. Visibility filtering matches the
    accounts list — accounts the caller can't see don't appear here.
    """
    effective_as_of = as_of or date_type.today()
    tx_repo = TransactionRepository(session, claims.household_id)
    account_repo = AccountRepository(session, claims.household_id)

    accounts_by_id = {a.id: a for a in account_repo.list_active()}
    raw = tx_repo.trial_balance(as_of=effective_as_of)

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

    json_body = TrialBalanceRead(as_of=effective_as_of, rows=rows, totals_by_currency=totals)
    if format == "html":
        from tulip_reports.reports import trial_balance as report_module

        data = report_module.build(
            session,
            household_id=claims.household_id,
            as_of=effective_as_of,
            visible_account_filter=lambda vis, by: _filter_for_role(vis, by, claims),
        )
        return HTMLResponse(content=report_module.render_html(data))
    return Response(
        content=json_body.model_dump_json(),
        media_type="application/json",
    )
