"""Relax ix_import_batches_idempotency from UNIQUE to non-unique (#114).

Revision ID: a4f1c8d3e9b7
Revises: d5c8e7a91f2b
Create Date: 2026-05-07 22:00:00.000000

P5.1 added the unique index ``ix_import_batches_idempotency`` on
``(household_id, account_id, source_file_attachment_id)`` to enforce
"can't re-import the same file for the same account." But ADR-0004 §Q6
spells out a ``?force=true`` override that creates a second batch
referencing the same attachment. The unique constraint blocks that
override at the DB level.

Fix: drop the UNIQUE flag from the index. The column ordering is still
useful for the duplicate-lookup query (``find_for_attachment``); the
constraint moves to application code, which raises
``import.duplicate_file`` on idempotency conflicts and skips the lookup
when ``?force=true`` is set.

The 409 happy path stays correct because the API already calls
``find_for_attachment`` before insert; the index just no longer blocks
``force=true``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4f1c8d3e9b7"
down_revision: str | None = "d5c8e7a91f2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the unique index, recreate as non-unique."""
    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.drop_index("ix_import_batches_idempotency")
        batch_op.create_index(
            "ix_import_batches_idempotency",
            ["household_id", "account_id", "source_file_attachment_id"],
            unique=False,
        )


def downgrade() -> None:
    """Restore the UNIQUE flag (caller must dedupe rows first)."""
    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.drop_index("ix_import_batches_idempotency")
        batch_op.create_index(
            "ix_import_batches_idempotency",
            ["household_id", "account_id", "source_file_attachment_id"],
            unique=True,
        )
