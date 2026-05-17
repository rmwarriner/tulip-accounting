"""hledger-compatible journal export + import (P7.4 / P7.5).

GET ``/v1/journal/export`` renders the household's posted transactions
as plain-text hledger journal text.

POST ``/v1/journal/import`` accepts a journal file body and creates
**pending** transactions ready for review — same convention as the
existing OFX / QIF / CSV importers (#74).
"""

from __future__ import annotations

from datetime import date as date_type
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import TulipProblem, problem_response

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/journal", tags=["journal"])


_MAX_JOURNAL_BYTES = 5 * 1024 * 1024  # 5 MB — matches the OFX cap (#74).


def _filter_for_role(account_visibility: str, created_by: object, claims: Claims) -> bool:
    """Mirror routers.reports._filter_for_role for journal-export filtering."""
    if account_visibility == "shared":
        return True
    if claims.role == "admin":
        return True
    return created_by == claims.user_id


class JournalParseFailedError(TulipProblem):
    """The uploaded journal couldn't be parsed (P7.5)."""

    def __init__(self, errors: list[dict[str, object]]) -> None:
        """Build the journal.parse_failed problem."""
        super().__init__(
            code="journal.parse_failed",
            title="Journal parsing failed",
            status=400,
            detail=(
                f"{len(errors)} parse error(s) in the uploaded journal. "
                "See ``errors`` for line numbers + messages."
            ),
            extensions={"errors": errors},
        )


class JournalImportFailedError(TulipProblem):
    """Resolution / validation failed during journal import (P7.5)."""

    def __init__(self, errors: list[dict[str, object]]) -> None:
        """Build the journal.import_failed problem."""
        super().__init__(
            code="journal.import_failed",
            title="Journal import failed",
            status=400,
            detail=(
                f"{len(errors)} error(s) resolving the journal. Each error "
                "names a line number + the issue (unknown account, balance "
                "mismatch, currency mismatch). Fix the journal or seed the "
                "missing accounts and retry."
            ),
            extensions={"errors": errors},
        )


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
    include_metadata: bool = Query(
        default=True,
        description=(
            "When true (default), the export's header comments name the "
            "household + Tulip provenance. Set false (privacy audit L-5 / "
            "L-17, #351) for handoffs to third parties — e.g. a tax "
            "preparer — where the household name shouldn't ride along."
        ),
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
        # #229: drop postings on private accounts the caller can't see.
        visible_account_filter=lambda vis, by: _filter_for_role(vis, by, claims),
        include_metadata=include_metadata,
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


@router.post(
    "/import",
    response_model=None,
    responses={
        201: {"description": "Journal parsed + resolved; pending transactions created."},
        400: problem_response(
            "journal.parse_failed",
            "journal.import_failed",
            "request.body_invalid",
            "request.payload_too_large",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
async def import_journal(
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Accept a hledger-format journal body; create pending transactions.

    The body is parsed via :func:`tulip_reports.journal.parse.parse_journal`
    and resolved against the household's chart of accounts via
    :func:`tulip_reports.journal.import_.resolve_journal`. Any parse or
    resolve errors short-circuit the import and return a typed Problem
    Details with per-line error annotations.

    On success, transactions land in **PENDING status** through the
    existing ``TransactionRepository.create`` chokepoint so the user
    reviews before promoting to POSTED. The response carries the
    created transaction IDs.
    """
    body = await request.body()
    if len(body) > _MAX_JOURNAL_BYTES:
        from tulip_api.errors import RequestPayloadTooLargeError

        raise RequestPayloadTooLargeError(max_bytes=_MAX_JOURNAL_BYTES)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JournalParseFailedError(
            errors=[{"line": 0, "message": f"journal body must be UTF-8: {exc}"}]
        ) from exc

    from tulip_reports.journal import parse_journal, resolve_journal

    parsed = parse_journal(text)
    if parsed.errors:
        raise JournalParseFailedError(
            errors=[{"line": e.line_number, "message": e.message} for e in parsed.errors]
        )

    resolved = resolve_journal(session, household_id=claims.household_id, parsed=parsed)
    if resolved.errors:
        raise JournalImportFailedError(
            errors=[{"line": e.line_number, "message": e.message} for e in resolved.errors]
        )

    # Insert as PENDING transactions via the repo chokepoint. PENDING
    # is the same convention the OFX / QIF / CSV importers use (#74) —
    # the user reviews before promoting to POSTED.
    from uuid import uuid4

    from tulip_core.money import Money
    from tulip_core.transactions import (
        Posting as DomainPosting,
    )
    from tulip_core.transactions import (
        Transaction as DomainTransaction,
    )
    from tulip_core.transactions import (
        TransactionStatus as DomainTxStatus,
    )
    from tulip_storage.repositories import TransactionRepository

    repo = TransactionRepository(session, claims.household_id)
    created_ids: list[str] = []
    for tx in resolved.transactions:
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=claims.household_id,
            date=tx.date,
            description=tx.description,
            reference=tx.reference,
            postings=tuple(
                DomainPosting(
                    id=uuid4(),
                    account_id=p.account_id,
                    amount=Money(p.amount, p.currency),
                )
                for p in tx.postings
            ),
            status=DomainTxStatus.PENDING,
            created_by_user_id=claims.user_id,
        )
        created = repo.save_balanced(domain_tx)
        created_ids.append(str(created.id))
    session.commit()

    import json as _json

    return Response(
        status_code=201,
        content=_json.dumps({"created": len(created_ids), "transaction_ids": created_ids}),
        media_type="application/json",
    )


__all__ = [
    "JournalImportFailedError",
    "JournalParseFailedError",
    "router",
]
