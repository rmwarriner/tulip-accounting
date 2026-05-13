"""Make reconciliation_matches.statement_line_id nullable.

Per #275: paper-statement (no-OFX) reconciliations don't have statement
lines — the user is ticking off ledger transactions against a physical
statement, with no imported batch. To represent "this tx is matched but
there's no line to point at", the column needs to be nullable.

The existing FK (composite on ``(household_id, statement_line_id) ->
statement_lines(household_id, id) ON DELETE CASCADE``) stays; SQLite
treats NULL components as "no reference", and the FK action only fires
when both values are non-NULL.

Revision ID: c1d4f7a2b9e6
Revises: b1c2d3e4f5a6
Create Date: 2026-05-13 17:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from tulip_storage.models.base import GUID

revision: str = "c1d4f7a2b9e6"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Relax statement_line_id NOT NULL on reconciliation_matches."""
    with op.batch_alter_table("reconciliation_matches", schema=None) as batch:
        batch.alter_column(
            "statement_line_id",
            existing_type=GUID(length=36),
            nullable=True,
        )


def downgrade() -> None:
    """Re-tighten statement_line_id to NOT NULL.

    Note: down-migration will fail if any rows exist with NULL
    statement_line_id (paper-statement matches). Callers must remove
    such rows first.
    """
    with op.batch_alter_table("reconciliation_matches", schema=None) as batch:
        batch.alter_column(
            "statement_line_id",
            existing_type=GUID(length=36),
            nullable=False,
        )
