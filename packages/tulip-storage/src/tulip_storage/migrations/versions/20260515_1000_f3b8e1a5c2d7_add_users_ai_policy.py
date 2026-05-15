"""Add users.ai_policy JSON column for per-user AI policy ratchet-up (#239).

Per ADR-0005 §Q5 the policy shape is household-floor + per-user-ratchet-up.
The merge logic in ``tulip_ai.policy.resolve_policy`` already accepts a
``user_policy`` argument and is tested for "max-severity wins"; this
column gives it storage to draw from.

Nullable, defaults NULL → the resolver's existing ``if user_policy: ...``
guard interprets NULL as "no per-user override; inherit household". This
is unlike ``households.ai_policy`` which is NOT NULL with default ``{}``
— for users, "no override" is the common case.

Revision ID: f3b8e1a5c2d7
Revises: a2e9c4f1d8b3
Create Date: 2026-05-15 10:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b8e1a5c2d7"
down_revision: str | None = "a2e9c4f1d8b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable JSON ``ai_policy`` column to ``users``."""
    with op.batch_alter_table("users", schema=None) as batch:
        batch.add_column(sa.Column("ai_policy", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop the ``ai_policy`` column from ``users``."""
    with op.batch_alter_table("users", schema=None) as batch:
        batch.drop_column("ai_policy")
