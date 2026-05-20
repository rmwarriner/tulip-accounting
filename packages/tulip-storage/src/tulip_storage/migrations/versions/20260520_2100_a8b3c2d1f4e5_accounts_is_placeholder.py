"""Add ``accounts.is_placeholder`` flag (#52).

A placeholder account is a non-posting node in the chart of accounts
— typically an organisational header like "Assets:Current Assets"
that's there to group its children, not to receive postings itself.

Today nothing enforces that distinction; users can post to any
account and the chart-of-accounts hygiene is on the operator. With
this column the API can reject any posting whose target account has
``is_placeholder=true``, prompting the user to pick a leaf instead.

The column defaults to ``false`` for every existing row, so no
existing behaviour changes — placeholder accounts are opt-in.

Revision ID: a8b3c2d1f4e5
Revises: f4d8c2a1b9e7
Create Date: 2026-05-20 21:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b3c2d1f4e5"
down_revision: str | None = "f4d8c2a1b9e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``is_placeholder BOOLEAN NOT NULL DEFAULT 0`` to ``accounts``."""
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column(
                "is_placeholder",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    """Drop the column."""
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("is_placeholder")
