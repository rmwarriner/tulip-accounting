"""Add used_mfa_challenges table.

Tracks redeemed MFA-challenge JWT ``jti`` values so a stolen or replayed
challenge token cannot be reused. Tiny by design — purged whenever
``expires_at`` is in the past. See M-7 in #219.

Revision ID: d4f7a9e1c8b3
Revises: a7d4f1b9e8c2
Create Date: 2026-05-13 11:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = "d4f7a9e1c8b3"
down_revision: str | None = "a7d4f1b9e8c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the used_mfa_challenges table."""
    op.create_table(
        "used_mfa_challenges",
        sa.Column("jti", GUID(length=36), nullable=False),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti", name=op.f("pk_used_mfa_challenges")),
    )
    with op.batch_alter_table("used_mfa_challenges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_used_mfa_challenges_expires_at"),
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    """Drop the used_mfa_challenges table."""
    with op.batch_alter_table("used_mfa_challenges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_used_mfa_challenges_expires_at"))
    op.drop_table("used_mfa_challenges")
