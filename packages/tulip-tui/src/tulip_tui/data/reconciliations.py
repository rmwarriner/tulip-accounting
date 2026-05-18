"""Reconciliations browse-list data adapter.

Wraps ``GET /v1/reconciliations`` into an immutable
``ReconciliationsData`` value object. The TUI v1 surfaces this as
read-only — *acting* on a reconciliation (auto-match, manual match,
complete) stays on the CLI per ADR-0007 and the design pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    """One reconciliation envelope as the browse screen needs it."""

    id: str
    account_id: str
    statement_period_start: str
    statement_period_end: str
    statement_starting_balance: str
    statement_ending_balance: str
    currency: str
    status: str
    source_import_batch_id: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationsData:
    """The full payload the reconciliations screen renders."""

    reconciliations: tuple[ReconciliationSummary, ...]


def load_reconciliations(client: TulipClient) -> ReconciliationsData:
    """Fetch and parse ``/v1/reconciliations``."""
    raw = client.get("/v1/reconciliations", authenticated=True).json()
    items = tuple(_to_summary(row) for row in raw)
    return ReconciliationsData(reconciliations=items)


def _to_summary(row: dict[str, object]) -> ReconciliationSummary:
    return ReconciliationSummary(
        id=str(row.get("id", "")),
        account_id=str(row.get("account_id", "")),
        statement_period_start=str(row.get("statement_period_start", "")),
        statement_period_end=str(row.get("statement_period_end", "")),
        statement_starting_balance=str(row.get("statement_starting_balance", "")),
        statement_ending_balance=str(row.get("statement_ending_balance", "")),
        currency=str(row.get("currency", "")),
        status=str(row.get("status", "")),
        source_import_batch_id=_optional_str(row.get("source_import_batch_id")),
        created_at=str(row.get("created_at", "")),
        completed_at=_optional_str(row.get("completed_at")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__: list[str] = [
    "ReconciliationSummary",
    "ReconciliationsData",
    "load_reconciliations",
]
