"""GET /v1/journal/export — hledger-compatible journal export (P7.4).

Renders the household's posted transactions as a plain-text hledger
journal file. The format is described in
``tulip_reports.journal.export`` — the API endpoint is a thin wrapper.

Pending and voided transactions are excluded, matching the trial-
balance / income-statement conventions.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from tulip_api.auth.deps import get_current_claims
from tulip_api.deps import get_session
from tulip_api.errors import problem_response

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/journal", tags=["journal"])


@router.get(
    "/export",
    response_model=None,
    responses={
        200: {
            "description": (
                "hledger-compatible journal file as text/plain. "
                "MIME type ``application/x-hledger-journal`` is also "
                "set so downloaders can recognise the format."
            ),
        },
        401: problem_response("auth.unauthorized"),
    },
)
def export(
    start: date_type | None = Query(  # noqa: B008 — FastAPI requires Query()
        default=None,
        description="Inclusive lower bound on transaction date (YYYY-MM-DD).",
    ),
    end: date_type | None = Query(  # noqa: B008
        default=None,
        description="Inclusive upper bound on transaction date (YYYY-MM-DD).",
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Return the household's posted ledger as a hledger journal.

    The response is ``text/plain`` (so browsers display it) with a
    ``Content-Disposition: attachment`` header so the CLI / curl
    download to a sensible filename by default.
    """
    from tulip_reports.journal.export import export_journal

    body = export_journal(
        session,
        household_id=claims.household_id,
        start=start,
        end=end,
    )
    suffix_parts = []
    if start is not None:
        suffix_parts.append(start.isoformat())
    if end is not None:
        suffix_parts.append(end.isoformat())
    suffix = "-".join(suffix_parts) if suffix_parts else "all"
    filename = f"tulip-journal-{suffix}.journal"
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Format": "hledger",
        },
    )


__all__ = ["router"]
