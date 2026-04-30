"""Add users.totp_enrolled_at column.

Distinguishes "TOTP secret stored, enrollment not yet verified" (column NULL,
totp_secret_encrypted non-NULL) from "TOTP active" (both non-NULL). Lets us
reject re-enrollment of an already-active user and lets login decide whether
to gate behind an MFA challenge.

Revision ID: 4934c5509a8d
Revises: 1e585c3bb01d
Create Date: 2026-04-30 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4934c5509a8d"
down_revision: str | None = "1e585c3bb01d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the totp_enrolled_at column."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("totp_enrolled_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """Drop the totp_enrolled_at column."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("totp_enrolled_at")
