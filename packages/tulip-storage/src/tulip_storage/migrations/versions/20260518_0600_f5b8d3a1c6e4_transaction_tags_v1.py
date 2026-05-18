"""Add transaction_tags table for the v1 labels-only tags surface (#39).

The first slice of #39 ships free-form string tags on transactions
only — no cascade, no key=value pairs, no account- or posting-level
tags. The schema is a separate `transaction_tags` table keyed on
``(household_id, transaction_id, tag)`` so the (household, tag)
filter lookup is index-served (``ix_transaction_tags_household_tag``)
and the per-transaction tag list is index-served via the composite PK.

Composite FK to ``transactions.(household_id, id)`` with
``ON DELETE CASCADE`` so deleting a transaction sweeps its tags
without leaving orphans. The composite shape also means a tenant
boundary breach would have to forge both columns — same posture as
the postings table.

Revision ID: f5b8d3a1c6e4
Revises: e9c4f1b7d2a5
Create Date: 2026-05-18 06:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5b8d3a1c6e4"
down_revision: str | None = "e9c4f1b7d2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``transaction_tags`` table + filter-friendly composite index."""
    op.create_table(
        "transaction_tags",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_transaction",
        ),
        sa.PrimaryKeyConstraint(
            "household_id", "transaction_id", "tag", name="pk_transaction_tags"
        ),
    )
    # Index that serves the ``GET /v1/transactions?tag=foo`` filter:
    # the API scopes to a household + filters by tag, and the index
    # prefix matches. Tied to the table — drops automatically on
    # downgrade.
    op.create_index(
        "ix_transaction_tags_household_tag",
        "transaction_tags",
        ["household_id", "tag"],
    )


def downgrade() -> None:
    """Drop the index then the table."""
    op.drop_index("ix_transaction_tags_household_tag", table_name="transaction_tags")
    op.drop_table("transaction_tags")
