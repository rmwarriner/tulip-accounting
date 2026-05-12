"""Audit-log report (P7.1).

Filtered, paginated view of ``audit_log`` rows. Filters: date range
(by ``occurred_at``), optional ``actor_user_id``, optional
``entity_type``. The page size is bounded so the rendered HTML stays
manageable; the API endpoint exposes ``limit``/``offset`` for pagination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_type
from typing import TYPE_CHECKING
from uuid import UUID

from tulip_reports.engine import get_renderer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_MAX_LIMIT = 500


@dataclass(frozen=True, slots=True)
class AuditLogRow:
    """One audit-log entry."""

    occurred_at: datetime
    actor_kind: str
    actor_user_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID | None
    request_id: UUID | None


@dataclass(frozen=True, slots=True)
class AuditLogData:
    """Everything the audit-log template needs to render."""

    rows: list[AuditLogRow]
    total_matching: int
    start: date_type | None
    end: date_type | None
    actor_user_id: UUID | None
    entity_type: str | None
    limit: int
    offset: int
    household_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build(
    session: Session,
    *,
    household_id: UUID,
    start: date_type | None = None,
    end: date_type | None = None,
    actor_user_id: UUID | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> AuditLogData:
    """Query the audit log with filters; return the rows + total count."""
    from sqlalchemy import func, select

    from tulip_storage.models import AuditLog, Household

    household = session.get(Household, household_id)
    assert household is not None  # noqa: S101
    bounded_limit = max(1, min(limit, _MAX_LIMIT))

    base = select(AuditLog).where(AuditLog.household_id == household_id)
    count_q = (
        select(func.count()).select_from(AuditLog).where(AuditLog.household_id == household_id)
    )
    if start is not None:
        bound = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        base = base.where(AuditLog.occurred_at >= bound)
        count_q = count_q.where(AuditLog.occurred_at >= bound)
    if end is not None:
        bound_end = datetime.combine(end, datetime.max.time(), tzinfo=UTC)
        base = base.where(AuditLog.occurred_at <= bound_end)
        count_q = count_q.where(AuditLog.occurred_at <= bound_end)
    if actor_user_id is not None:
        base = base.where(AuditLog.actor_user_id == actor_user_id)
        count_q = count_q.where(AuditLog.actor_user_id == actor_user_id)
    if entity_type is not None:
        base = base.where(AuditLog.entity_type == entity_type)
        count_q = count_q.where(AuditLog.entity_type == entity_type)
    base = base.order_by(AuditLog.occurred_at.desc()).limit(bounded_limit).offset(max(offset, 0))

    rows = [
        AuditLogRow(
            occurred_at=row.occurred_at,
            actor_kind=row.actor_kind,
            actor_user_id=row.actor_user_id,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            request_id=row.request_id,
        )
        for row in session.execute(base).scalars().all()
    ]
    total = int(session.execute(count_q).scalar_one() or 0)

    return AuditLogData(
        rows=rows,
        total_matching=total,
        start=start,
        end=end,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        limit=bounded_limit,
        offset=max(offset, 0),
        household_name=household.name,
    )


def render_html(data: AuditLogData) -> str:
    """Render the audit-log data as HTML."""
    return get_renderer().render(
        "audit_log.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_pdf(data: AuditLogData) -> bytes:
    """Render the report as PDF bytes via weasyprint (P7.2)."""
    return get_renderer().render_pdf(
        "audit_log.html",
        data=data,
        generated_at=data.generated_at,
    )


def render_csv(data: AuditLogData) -> bytes:
    """Render audit log as CSV (P7.3): one row per audit entry."""
    from tulip_reports.engine import ReportRenderer

    headers = [
        "Occurred at",
        "Actor kind",
        "Actor user id",
        "Action",
        "Entity type",
        "Entity id",
        "Request id",
    ]
    rows: list[list[object]] = [
        [
            row.occurred_at.isoformat(),
            row.actor_kind,
            row.actor_user_id or "",
            row.action,
            row.entity_type,
            row.entity_id or "",
            row.request_id or "",
        ]
        for row in data.rows
    ]
    return ReportRenderer.csv_bytes(headers, rows)


__all__ = [
    "AuditLogData",
    "AuditLogRow",
    "build",
    "render_csv",
    "render_html",
    "render_pdf",
]
