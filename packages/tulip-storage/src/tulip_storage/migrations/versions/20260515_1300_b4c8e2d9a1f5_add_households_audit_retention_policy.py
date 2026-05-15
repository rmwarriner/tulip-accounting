"""Add households.audit_retention_policy JSON column (#245).

Per the deep privacy audit's M-1: ``audit_log`` rows survive deletion of
the underlying entity (PENDING delete leaves description in
``before_snapshot`` forever). GDPR Art. 5(1)(e) storage-limitation
needs purpose-bound retention; "forever" is not policy.

The new column mirrors ``households.ai_policy`` exactly: NOT NULL JSON
with ``server_default='{}'``. Empty dict means "fall through to code
defaults" — the resolver in
``tulip_storage.runner.handlers.audit_retention`` reads via
``.get(key, _TIER_DEFAULTS[key])`` so a fresh install runs against the
audit-recommended numbers (7y / 90d / 30d / 365d / 90d) without any
operator action.

Revision ID: b4c8e2d9a1f5
Revises: f3b8e1a5c2d7
Create Date: 2026-05-15 13:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c8e2d9a1f5"
down_revision: str | None = "f3b8e1a5c2d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the NOT-NULL JSON ``audit_retention_policy`` column to ``households``."""
    with op.batch_alter_table("households", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "audit_retention_policy",
                sa.JSON(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade() -> None:
    """Drop the ``audit_retention_policy`` column from ``households``."""
    with op.batch_alter_table("households", schema=None) as batch:
        batch.drop_column("audit_retention_policy")
