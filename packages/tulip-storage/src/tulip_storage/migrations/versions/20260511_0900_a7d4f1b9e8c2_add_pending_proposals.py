"""Add pending_proposals table (P6.4 — agentic proposals).

Per ADR-0005 §Q9 + ARCHITECTURE.md §6.2. One row per AI- (or user-)
proposed change awaiting explicit user approval. The approve flow
executes the change through the same domain path a user-initiated
change would take, with the audit_log row noting ``actor_kind=ai_agent``
and the originating proposal id.

The ``kind`` column is a free-form string today; v1 only ``envelope_budget_update``
is wired end-to-end. Adding kinds is a code change (new executor) +
migration-free (just a new string value).

Revision ID: a7d4f1b9e8c2
Revises: e3a1c5d7b9f2
Create Date: 2026-05-11 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d4f1b9e8c2"
down_revision: str | None = "e3a1c5d7b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the pending_proposals table."""
    op.create_table(
        "pending_proposals",
        sa.Column("household_id", sa.CHAR(32), nullable=False),
        sa.Column("id", sa.CHAR(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # kind: envelope_budget_update / categorize_lines / transfer_pools / ...
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False, server_default=""),
        # payload: kind-specific JSON shape the executor interprets.
        sa.Column("payload", sa.JSON, nullable=False),
        # status: pending / approved / rejected
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        # created_by_kind: user / ai_agent — drives the actor_kind written
        # to audit_log when the proposal is approved (per ARCHITECTURE.md §6.2).
        sa.Column("created_by_kind", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.CHAR(32), nullable=True),
        # ai_invocation_id: links AI-generated proposals back to the
        # ai_invocations row that produced them. NULL for user-created.
        sa.Column("ai_invocation_id", sa.CHAR(32), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("decided_by_user_id", sa.CHAR(32), nullable=True),
        # Free-text reason from the user, if they chose to record one on
        # reject/approve. Optional.
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("household_id", "id"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_pending_proposals_household_status",
        "pending_proposals",
        ["household_id", "status"],
    )


def downgrade() -> None:
    """Drop the pending_proposals table."""
    op.drop_index("ix_pending_proposals_household_status", table_name="pending_proposals")
    op.drop_table("pending_proposals")
