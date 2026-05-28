"""Rename import_batches.source_format value 'journal' → 'pta_hledger' (#415).

The /v1/journal/import endpoint created PENDING transactions directly
rather than writing ImportBatch rows, so this backfill is a no-op for
every real deployment. The UPDATE is included for correctness — any
batch row that somehow carries the old value is migrated forward.

Revision ID: e1f7a3c9b5d2
Revises: d3e8b1a9f5c2
Create Date: 2026-05-24 10:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f7a3c9b5d2"
down_revision: str | None = "d3e8b1a9f5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename source_format value 'journal' → 'pta_hledger'."""
    op.execute(
        sa.text(
            "UPDATE import_batches SET source_format = 'pta_hledger'"
            " WHERE source_format = 'journal'"
        )
    )


def downgrade() -> None:
    """Reverse: rename source_format value 'pta_hledger' → 'journal'."""
    op.execute(
        sa.text(
            "UPDATE import_batches SET source_format = 'journal'"
            " WHERE source_format = 'pta_hledger'"
        )
    )
