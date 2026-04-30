"""Add households.mfa_policy column.

Per ARCHITECTURE §4.1 the household carries an MFA policy controlling
whether members must enroll in TOTP. The login challenge gate (P2.x.1.b)
branches on this column. New rows default to 'optional' so existing
deployments don't lock anyone out on upgrade.

Revision ID: 1ab98df73aba
Revises: 4934c5509a8d
Create Date: 2026-04-30 14:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1ab98df73aba"
down_revision: str | None = "4934c5509a8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the mfa_policy column with a server-side default of 'optional'."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "mfa_policy",
                sa.String(length=30),
                nullable=False,
                server_default="optional",
            )
        )


def downgrade() -> None:
    """Drop the mfa_policy column."""
    with op.batch_alter_table("households", schema=None) as batch_op:
        batch_op.drop_column("mfa_policy")
