"""Add posting_tags + account_tags tables (ADR-0009, PR B).

Layered on top of the PR A normalisation (b2c4f9a1e7d6). Adds
the two remaining tag-scope edge tables:

- ``posting_tags(household_id, posting_id, tag_id)``
- ``account_tags(household_id, account_id, tag_id)``

No data migration — both tables start empty. Downgrade drops
them cleanly.

Revision ID: d3e8b1a9f5c2
Revises: b2c4f9a1e7d6
Create Date: 2026-05-21 01:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e8b1a9f5c2"
down_revision: str | None = "b2c4f9a1e7d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``posting_tags`` + ``account_tags``."""
    op.create_table(
        "posting_tags",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        # postings.id is the single-column PK on postings, so the
        # posting-side FK is single-column. Tenant isolation on this
        # side is enforced at the repo level (always filter by
        # household_id).
        sa.ForeignKeyConstraint(
            ["posting_id"],
            ["postings.id"],
            ondelete="CASCADE",
            name="fk_posting_tags_posting",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "tag_id"],
            ["tags.household_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_posting_tags_tag",
        ),
        sa.PrimaryKeyConstraint("household_id", "posting_id", "tag_id", name="pk_posting_tags"),
    )
    op.create_table(
        "account_tags",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            ondelete="CASCADE",
            name="fk_account_tags_account",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "tag_id"],
            ["tags.household_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_account_tags_tag",
        ),
        sa.PrimaryKeyConstraint("household_id", "account_id", "tag_id", name="pk_account_tags"),
    )


def downgrade() -> None:
    """Drop ``posting_tags`` + ``account_tags``."""
    op.drop_table("account_tags")
    op.drop_table("posting_tags")
