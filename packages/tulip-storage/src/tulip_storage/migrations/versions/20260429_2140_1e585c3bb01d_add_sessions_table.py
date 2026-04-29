"""Add sessions table for refresh-token-backed authentication.

Revision ID: 1e585c3bb01d
Revises: c2f963036df3
Create Date: 2026-04-29 21:40:45.914040+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = "1e585c3bb01d"
down_revision: str | None = "c2f963036df3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the sessions table + indexes."""
    op.create_table(
        "sessions",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("user_id", GUID(length=36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "user_id"],
            ["users.household_id", "users.id"],
            name="fk_sessions_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_sessions_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("refresh_token_hash", name=op.f("uq_sessions_refresh_token_hash")),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sessions_household_id"), ["household_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Drop the sessions table + indexes."""
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_sessions_household_id"))
    op.drop_table("sessions")
