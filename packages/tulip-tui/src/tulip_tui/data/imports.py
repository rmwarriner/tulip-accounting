"""Import-batch browse-list data adapter.

Wraps ``GET /v1/imports`` into an immutable ``ImportsData`` value
object. The TUI v1 surfaces this as read-only — *applying* /
*reverting* a batch stays on the CLI per ADR-0007.
"""

from __future__ import annotations

from dataclasses import dataclass

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class ImportBatchSummary:
    """One import batch as the browse screen needs it."""

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


@dataclass(frozen=True, slots=True)
class ImportsData:
    """The full payload the imports screen renders."""

    batches: tuple[ImportBatchSummary, ...]


def load_import_batches(client: TulipClient) -> ImportsData:
    """Fetch and parse ``/v1/imports``."""
    raw = client.get("/v1/imports", authenticated=True).json()
    items = tuple(_to_summary(row) for row in raw)
    return ImportsData(batches=items)


def _to_summary(row: dict[str, object]) -> ImportBatchSummary:
    return ImportBatchSummary(
        id=str(row.get("id", "")),
        account_id=str(row.get("account_id", "")),
        source_format=str(row.get("source_format", "")),
        source_filename=str(row.get("source_filename", "")),
        status=str(row.get("status", "")),
        imported_count=_optional_int(row.get("imported_count")),
        skipped_count=_optional_int(row.get("skipped_count")),
        error_count=_optional_int(row.get("error_count")),
        created_at=str(row.get("created_at", "")),
        applied_at=_optional_str(row.get("applied_at")),
        reverted_at=_optional_str(row.get("reverted_at")),
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
    "ImportBatchSummary",
    "ImportsData",
    "load_import_batches",
]
