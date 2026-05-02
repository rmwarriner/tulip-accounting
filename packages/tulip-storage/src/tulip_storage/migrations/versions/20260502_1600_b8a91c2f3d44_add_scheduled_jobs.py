"""Add scheduled_jobs and scheduled_job_runs tables (P4.3.a / ADR-0002).

Two-table model:
- ``scheduled_jobs``: the schedule itself (kind, payload, rrule, next_run_at,
  is_active, idempotency_key). Distinct from ``audit_log`` — operational
  state belongs in ``scheduled_job_runs``, not in audit.
- ``scheduled_job_runs``: per-fire run records (status, retry_count,
  last_error). Only the runner module writes here.

Revision ID: b8a91c2f3d44
Revises: a3f4d8e91b22
Create Date: 2026-05-02 16:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

revision: str = "b8a91c2f3d44"
down_revision: str | None = "a3f4d8e91b22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scheduled_jobs + scheduled_job_runs."""
    op.create_table(
        "scheduled_jobs",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=True),
        # Original RRULE anchor; preserved across runs so COUNT / UNTIL
        # semantics stay stable. See ADR-0002 §5.
        sa.Column("dtstart", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_by_user_id", GUID(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_scheduled_jobs_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_scheduled_jobs")),
    )
    with op.batch_alter_table("scheduled_jobs", schema=None) as batch_op:
        # Polling index — runner queries WHERE is_active=1 AND next_run_at <= now.
        batch_op.create_index(
            "ix_scheduled_jobs_next_run",
            ["next_run_at"],
            unique=False,
            sqlite_where=sa.text("is_active = 1"),
        )
        # Idempotency: per-household, per-kind. SQLite supports partial unique
        # indexes — Postgres will use the same WHERE clause when we get there.
        batch_op.create_index(
            "ix_scheduled_jobs_idempotency",
            ["household_id", "kind", "idempotency_key"],
            unique=True,
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        )

    op.create_table(
        "scheduled_job_runs",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("scheduled_job_id", GUID(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "success",
                "failed",
                "dead_letter",
                name="scheduledjobrunstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "scheduled_job_id"],
            ["scheduled_jobs.household_id", "scheduled_jobs.id"],
            name="fk_scheduled_job_runs_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_scheduled_job_runs")),
    )
    with op.batch_alter_table("scheduled_job_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scheduled_job_runs_scheduled_job_id"),
            ["scheduled_job_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop scheduled_job_runs + scheduled_jobs."""
    with op.batch_alter_table("scheduled_job_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scheduled_job_runs_scheduled_job_id"))
    op.drop_table("scheduled_job_runs")

    with op.batch_alter_table("scheduled_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_scheduled_jobs_idempotency")
        batch_op.drop_index("ix_scheduled_jobs_next_run")
    op.drop_table("scheduled_jobs")
