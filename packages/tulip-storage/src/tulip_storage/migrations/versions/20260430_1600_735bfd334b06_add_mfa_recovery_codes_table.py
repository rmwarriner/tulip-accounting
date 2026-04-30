"""Add mfa_recovery_codes table.

Stores argon2id-hashed single-use recovery codes for users who lose
access to their authenticator app. Generated on TOTP enrollment-verify;
each row is consumed at most once via /v1/auth/login/recover. Used rows
are kept for audit reconstruction (don't delete on consumption).

Revision ID: 735bfd334b06
Revises: 1ab98df73aba
Create Date: 2026-04-30 16:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = "735bfd334b06"
down_revision: str | None = "1ab98df73aba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the mfa_recovery_codes table."""
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("user_id", GUID(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "user_id"],
            ["users.household_id", "users.id"],
            name="fk_mfa_recovery_codes_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_mfa_recovery_codes_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_recovery_codes")),
    )
    with op.batch_alter_table("mfa_recovery_codes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mfa_recovery_codes_household_id"), ["household_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mfa_recovery_codes_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    """Drop the mfa_recovery_codes table."""
    with op.batch_alter_table("mfa_recovery_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_mfa_recovery_codes_user_id"))
        batch_op.drop_index(batch_op.f("ix_mfa_recovery_codes_household_id"))
    op.drop_table("mfa_recovery_codes")
