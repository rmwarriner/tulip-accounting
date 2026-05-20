"""Import-batch detail-view data adapter (P9.6.a).

Wraps ``GET /v1/imports/{batch_id}`` into an immutable
``ImportBatchDetail`` value object the detail screen renders. Lines
are sorted in source-file order (already done by the API) and carry a
derived ``status`` enum string so the table can render
``excluded`` / ``promoted`` / ``pending`` markers without re-deriving.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class StatementLineSummary:
    """One row in the batch detail table, with display strings precomputed."""

    id: str
    line_number: int
    date: str
    description: str
    amount_display: str
    currency: str
    is_excluded: bool
    promoted_transaction_id: str | None
    reconciliation_match_id: str | None

    @property
    def status(self) -> str:
        """``promoted`` > ``excluded`` > ``pending`` (display order)."""
        if self.promoted_transaction_id:
            return "promoted"
        if self.is_excluded:
            return "excluded"
        return "pending"


@dataclass(frozen=True, slots=True)
class ImportBatchDetail:
    """One import batch as the detail screen needs it."""

    id: str
    account_id: str
    source_format: str
    source_filename: str
    status: str
    imported_count: int
    skipped_count: int
    error_count: int
    created_at: str
    applied_at: str | None
    reverted_at: str | None
    lines: tuple[StatementLineSummary, ...]

    @property
    def pending_count(self) -> int:
        """Number of lines that would be promoted by a whole-batch apply."""
        return sum(1 for ln in self.lines if ln.status == "pending")

    @property
    def excluded_count(self) -> int:
        """Number of lines the user has marked excluded."""
        return sum(1 for ln in self.lines if ln.status == "excluded")

    @property
    def promoted_count(self) -> int:
        """Number of lines already promoted to ledger transactions."""
        return sum(1 for ln in self.lines if ln.status == "promoted")


def load_import_batch_detail(client: TulipClient, batch_id: str) -> ImportBatchDetail:
    """Fetch and parse ``/v1/imports/{batch_id}``."""
    raw = client.get(f"/v1/imports/{batch_id}", authenticated=True).json()
    lines_raw = cast("list[dict[str, object]]", raw.get("lines") or [])
    lines = tuple(_to_line_summary(row) for row in lines_raw)
    return ImportBatchDetail(
        id=str(raw.get("id", "")),
        account_id=str(raw.get("account_id", "")),
        source_format=str(raw.get("source_format", "")),
        source_filename=str(raw.get("source_filename", "")),
        status=str(raw.get("status", "")),
        imported_count=_optional_int(raw.get("imported_count")),
        skipped_count=_optional_int(raw.get("skipped_count")),
        error_count=_optional_int(raw.get("error_count")),
        created_at=str(raw.get("created_at", "")),
        applied_at=_optional_str(raw.get("applied_at")),
        reverted_at=_optional_str(raw.get("reverted_at")),
        lines=lines,
    )


def patch_line_excluded(
    client: TulipClient, batch_id: str, line_id: str, *, is_excluded: bool
) -> None:
    """Call the ``PATCH /v1/imports/{batch_id}/lines/{line_id}`` toggle."""
    client.patch(
        f"/v1/imports/{batch_id}/lines/{line_id}",
        authenticated=True,
        json={"is_excluded": is_excluded},
    )


def promote_line(client: TulipClient, batch_id: str, line_id: str) -> None:
    """Call the ``POST /v1/imports/{batch_id}/lines/{line_id}/promote`` action."""
    client.post(
        f"/v1/imports/{batch_id}/lines/{line_id}/promote",
        authenticated=True,
    )


def apply_batch(
    client: TulipClient,
    batch_id: str,
    *,
    as_posted: bool = False,
    no_categorize: bool = False,
    treat_cleared_as_pending: bool = False,
) -> dict[str, object]:
    """Call the ``POST /v1/imports/{batch_id}/apply`` action with the three toggles."""
    params: dict[str, str] = {}
    if as_posted:
        params["as_posted"] = "true"
    if no_categorize:
        params["no_categorize"] = "true"
    if treat_cleared_as_pending:
        params["treat_cleared_as_pending"] = "true"
    resp = client.request(
        "POST",
        f"/v1/imports/{batch_id}/apply",
        authenticated=True,
        params=params,
    )
    return cast("dict[str, object]", resp.json())


def _to_line_summary(row: dict[str, object]) -> StatementLineSummary:
    amount = Decimal(str(row.get("amount", "0"))).quantize(Decimal("0.01"))
    currency = str(row.get("currency", ""))
    return StatementLineSummary(
        id=str(row.get("id", "")),
        line_number=_optional_int(row.get("line_number")),
        date=str(row.get("posted_date", "")),
        description=str(row.get("description", "")),
        amount_display=f"{amount:,.2f}",
        currency=currency,
        is_excluded=bool(row.get("is_excluded", False)),
        promoted_transaction_id=_optional_str(row.get("promoted_transaction_id")),
        reconciliation_match_id=_optional_str(row.get("reconciliation_match_id")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


__all__: list[str] = [
    "ImportBatchDetail",
    "StatementLineSummary",
    "apply_batch",
    "load_import_batch_detail",
    "patch_line_excluded",
    "promote_line",
]
