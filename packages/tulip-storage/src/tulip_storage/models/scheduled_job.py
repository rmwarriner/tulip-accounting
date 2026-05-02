"""ScheduledJob and ScheduledJobRun models — see ADR-0002.

The runner reads ``scheduled_jobs`` for due rows and writes ``scheduled_job_runs``
to record per-fire operational state. Both tables are distinct from
``audit_log`` — audit captures user-visible side effects (one row per
materialized shadow tx), runs capture runner internals (retry counts,
last error, dead-letter status).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class ScheduledJobRunStatus(Enum):
    """Outcome of a single fire of a ``scheduled_jobs`` row.

    - ``RUNNING``: handler started but hasn't returned yet.
    - ``SUCCESS``: handler returned without raising.
    - ``FAILED``: handler raised; will retry per ADR-0002 §7.
    - ``DEAD_LETTER``: third failure; parent job ``is_active`` flipped to False.
    """

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class ScheduledJob(Base):
    """A scheduled job — the runner's polling target. See ADR-0002 §3."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        # Partial unique index — idempotency-key collisions (per
        # household, per kind) are rejected by the DB. Migrations create
        # the same index; this declaration ensures Base.metadata.create_all
        # in tests also creates it.
        Index(
            "ix_scheduled_jobs_idempotency",
            "household_id",
            "kind",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_scheduled_jobs_next_run",
            "next_run_at",
            sqlite_where=text("is_active = 1"),
        ),
    )

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The original anchor for the RRULE — needed so COUNT/UNTIL semantics
    # remain stable as ``next_run_at`` advances. Always equal to the
    # ``start_at`` passed to ``schedule_recurring`` (or ``fire_at`` for
    # one-shot jobs, where it's unused).
    dtstart: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    household: Mapped[Household] = relationship()


class ScheduledJobRun(Base):
    """One per-fire run record. See ADR-0002 §3."""

    __tablename__ = "scheduled_job_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "scheduled_job_id"],
            ["scheduled_jobs.household_id", "scheduled_jobs.id"],
            ondelete="CASCADE",
            name="fk_scheduled_job_runs_job",
        ),
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'dead_letter')",
            name="ck_scheduled_job_runs_status",
        ),
    )

    household_id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    scheduled_job_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ScheduledJobRunStatus] = mapped_column(
        SAEnum(
            ScheduledJobRunStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
