"""Add pending_household_erasures table.

Two-step confirmation for ``DELETE /v1/households/me`` (right-to-erasure).
See H-2 in #235.

Revision ID: e8b5c2a9d1f4
Revises: d4f7a9e1c8b3
Create Date: 2026-05-13 14:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = "e8b5c2a9d1f4"
down_revision: str | None = "d4f7a9e1c8b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the pending_household_erasures table."""
    op.create_table(
        "pending_household_erasures",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by_user_id", GUID(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_pending_household_erasures_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", name=op.f("pk_pending_household_erasures")),
    )


def downgrade() -> None:
    """Drop the pending_household_erasures table."""
    op.drop_table("pending_household_erasures")
