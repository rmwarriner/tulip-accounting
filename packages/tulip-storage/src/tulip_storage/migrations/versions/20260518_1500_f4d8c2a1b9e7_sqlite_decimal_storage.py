"""Rewrite money columns from REAL to scaled INTEGER on SQLite (#395).

The :class:`tulip_storage.models.base.SqliteDecimal` TypeDecorator now
binds Decimal values as scaled INT64 on SQLite so the per-currency
balance triggers (``trg_transactions_balanced_on_post`` et al.) see
exact integer arithmetic. Existing user databases hold the old
REAL-affinity values, however, so this migration rewrites them in
place.

SQLite's per-row dynamic typing means no DDL change is required —
the column type stays NUMERIC, but every value going forward is an
integer scaled by ``10**scale``. The UPDATE below converts each
money column's existing REAL values to the scaled INT representation
the decorator now expects on read.

The migration is a no-op on non-SQLite dialects: Postgres NUMERIC
arithmetic is already exact, and the decorator passes Decimal
through unchanged there.

Revision ID: f4d8c2a1b9e7
Revises: f5b8d3a1c6e4
Create Date: 2026-05-18 15:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f4d8c2a1b9e7"
down_revision: str | None = "f5b8d3a1c6e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, scale). Mirrors every column declared with
# ``SqliteDecimal(precision, scale)`` in tulip_storage.models. Adding a
# new money column? Append it here AND wire it through the decorator;
# ``test_architecture_no_raw_numeric_money`` blocks the regression.
_MONEY_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("postings", "amount", 8),
    ("postings", "fx_rate", 8),
    ("postings", "fx_amount", 8),
    ("shadow_postings", "amount", 8),
    ("statement_lines", "amount", 8),
    ("reconciliations", "statement_starting_balance", 8),
    ("reconciliations", "statement_ending_balance", 8),
    ("reconciliation_matches", "match_amount", 8),
    ("envelopes", "budget_amount", 8),
    ("sinking_funds", "target_amount", 8),
    ("sinking_funds", "contribution_amount", 8),
    ("ai_invocations", "cost_estimate_usd", 6),
)


def upgrade() -> None:
    """REAL → scaled INT64 for every money column on SQLite."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for table, column, scale in _MONEY_COLUMNS:
        factor = 10**scale
        # Table + column names come from the hard-coded _MONEY_COLUMNS
        # tuple — never user input — so the f-string is safe.
        sql = (
            f"UPDATE {table} SET {column} = CAST(ROUND({column} * {factor}) AS INTEGER) "  # noqa: S608
            f"WHERE {column} IS NOT NULL"
        )
        op.execute(sql)


def downgrade() -> None:
    """Scaled INT64 → REAL.

    Lossy in principle (you can't un-round) but the inverse of upgrade
    against the values upgrade itself produced: each integer ``n``
    becomes ``n * 1.0 / factor``, which the driver stores as REAL.
    """
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for table, column, scale in _MONEY_COLUMNS:
        factor = 10**scale
        sql = (
            f"UPDATE {table} SET {column} = ({column} * 1.0) / {factor} WHERE {column} IS NOT NULL"  # noqa: S608
        )
        op.execute(sql)
