"""Add notifications table (P6.3 — forecasting + anomaly detection).

Per ARCHITECTURE.md §6.2 + ADR-0005 §Q9: a household-scoped table that
the daily insights handler writes to (one row per anomaly + one row per
forecast). The user reads via ``tulip notifications list`` and dismisses
individual rows when handled.

The shape is deliberately minimal — kind / severity / body / produced_by
columns, no rendering policy in the schema. The CLI / API render the
body to whatever format the user wants.

Revision ID: e3a1c5d7b9f2
Revises: c6e9f2a17b8d
Create Date: 2026-05-11 06:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a1c5d7b9f2"
down_revision: str | None = "c6e9f2a17b8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the notifications table."""
    op.create_table(
        "notifications",
        sa.Column("household_id", sa.CHAR(32), nullable=False),
        sa.Column("id", sa.CHAR(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # kind: anomaly / forecast / runout / on_track / etc.
        sa.Column("kind", sa.String(length=40), nullable=False),
        # severity: info / warning / critical
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        # produced_by: which handler / capability wrote this row. Useful
        # for filtering ("show me only AI forecasts") and for follow-up
        # dedup logic.
        sa.Column("produced_by", sa.String(length=40), nullable=False),
        # entity_type / entity_id link back to the originating record
        # (e.g., envelope_id for envelope-runout forecasts).
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.CHAR(32), nullable=True),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # FK to ai_invocations for AI-generated rows so a user can trace
        # the forecast back to its audit trail.
        sa.Column("ai_invocation_id", sa.CHAR(32), nullable=True),
        sa.PrimaryKeyConstraint("household_id", "id"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_notifications_household_created",
        "notifications",
        ["household_id", "created_at"],
    )
    # Listing the inbox filters dismissed_at IS NULL; index targets that.
    op.create_index(
        "ix_notifications_household_dismissed",
        "notifications",
        ["household_id", "dismissed_at"],
    )


def downgrade() -> None:
    """Drop the notifications table."""
    op.drop_index("ix_notifications_household_dismissed", table_name="notifications")
    op.drop_index("ix_notifications_household_created", table_name="notifications")
    op.drop_table("notifications")
