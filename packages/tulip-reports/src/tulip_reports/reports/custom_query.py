"""Custom-query report (P7.1).

Accepts a read-only SQL ``SELECT`` against the AI views (P6.2) and
renders the result rows as a table. The query is run through
``tulip_ai.sql_safety.validate_and_rewrite`` so the same allow-list
that gates the NL-query capability also gates this report — the user
can compose arbitrary queries but cannot bypass tenant scoping or
emit writes.

This avoids re-implementing query parsing / safety logic and ensures
the audit-side guarantees of the AI surface apply here too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from tulip_reports.engine import get_renderer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_MAX_ROWS = 1000


@dataclass(frozen=True, slots=True)
class CustomQueryData:
    """Everything the custom-query template needs to render."""

    sql: str
    safe_sql: str
    columns: list[str]
    rows: list[list[object]]
    truncated: bool
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build(
    session: Session,
    *,
    household_id: UUID,
    sql: str,
) -> CustomQueryData:
    """Validate + rewrite ``sql`` via the AI safety layer; execute; return rows."""
    from sqlalchemy import text

    from tulip_ai.sql_safety import validate_and_rewrite
    from tulip_storage.models import Household

    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101

    safe = validate_and_rewrite(sql, household_id=str(household_id))
    result = session.execute(text(safe.sql), safe.parameters)
    raw_rows = result.fetchmany(_MAX_ROWS + 1)
    truncated = len(raw_rows) > _MAX_ROWS
    if truncated:
        raw_rows = raw_rows[:_MAX_ROWS]
    columns = list(result.keys()) if result.keys() is not None else []
    rows = [list(r) for r in raw_rows]

    return CustomQueryData(
        sql=sql,
        safe_sql=safe.sql,
        columns=columns,
        rows=rows,
        truncated=truncated,
        household_name=household.name,
    )


def render_html(data: CustomQueryData) -> str:
    """Render the custom-query data as HTML."""
    return get_renderer().render(
        "custom_query.html",
        data=data,
        generated_at=data.generated_at,
    )


__all__ = ["CustomQueryData", "build", "render_html"]
