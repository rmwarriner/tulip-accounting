"""Add transactions.voided_by_transaction_id + voided_at (P5.0).

Per ADR-0004 §"What P5.0 ships". The void mechanic for POSTED transactions
records the reversal sibling via a self-FK on the source row. ``voided_at``
is the timestamp when the link was established. Both columns are nullable
since most transactions are never voided.

The composite self-FK requires a SQLite table rebuild via batch_alter_table,
which conflicts with the main-ledger balance triggers (they reference
``transactions`` and ``postings`` by name). Drop the triggers around the
rebuild and recreate them — same dance as P4.0's
``20260502_1200_a3f4d8e91b22_add_allocation_shadow_ledger.py``.

Revision ID: e7d2a4f8c1b9
Revises: b8a91c2f3d44
Create Date: 2026-05-04 20:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.migrations._triggers import (
    INITIAL_TRIGGER_NAMES,
    INITIAL_TRIGGERS,
)
from tulip_storage.models.base import GUID

revision: str = "e7d2a4f8c1b9"
down_revision: str | None = "b8a91c2f3d44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two void-link columns + composite self-FK on transactions."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger_name in reversed(INITIAL_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("voided_by_transaction_id", GUID(length=36), nullable=True))
        batch_op.add_column(sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_transactions_voided_by",
            "transactions",
            ["household_id", "voided_by_transaction_id"],
            ["household_id", "id"],
            use_alter=True,
        )

    if bind.dialect.name == "sqlite":
        for ddl in INITIAL_TRIGGERS:
            op.execute(ddl)


def downgrade() -> None:
    """Drop the void-link columns + the self-FK."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger_name in reversed(INITIAL_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_transactions_voided_by", type_="foreignkey")
        batch_op.drop_column("voided_at")
        batch_op.drop_column("voided_by_transaction_id")

    if bind.dialect.name == "sqlite":
        for ddl in INITIAL_TRIGGERS:
            op.execute(ddl)
