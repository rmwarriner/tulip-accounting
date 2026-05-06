"""Add statement_lines.promoted_transaction_id (P5.4.a).

Revision ID: d5c8e7a91f2b
Revises: f4a6b9c2e7d3
Create Date: 2026-05-06 12:00:00.000000

The promote endpoint (POST /v1/imports/{batch_id}/lines/{line_id}/promote)
turns a parsed statement line into a PENDING ledger transaction. We need
an O(1) idempotency check: "has this line already been promoted?" rather
than a back-scan through ``transactions.imported_from_id``. A nullable
composite FK on ``statement_lines`` keeps the lookup at
``WHERE id = ? AND promoted_transaction_id IS NOT NULL``.

The FK target is the existing ``transactions(household_id, id)`` PK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

revision: str = "d5c8e7a91f2b"
down_revision: str | None = "f4a6b9c2e7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``promoted_transaction_id`` + composite FK."""
    with op.batch_alter_table("statement_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("promoted_transaction_id", GUID(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_statement_lines_promoted_tx",
            "transactions",
            ["household_id", "promoted_transaction_id"],
            ["household_id", "id"],
            use_alter=True,
        )


def downgrade() -> None:
    """Drop the FK + column."""
    with op.batch_alter_table("statement_lines", schema=None) as batch_op:
        batch_op.drop_constraint("fk_statement_lines_promoted_tx", type_="foreignkey")
        batch_op.drop_column("promoted_transaction_id")
