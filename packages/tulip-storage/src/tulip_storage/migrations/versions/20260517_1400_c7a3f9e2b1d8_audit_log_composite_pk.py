"""Composite primary key on ``audit_log`` (#337, audit M-12).

Promotes ``audit_log.id`` to a composite ``(household_id, id)`` PK so
the household-scoped-model pattern from ARCHITECTURE §3.3 is enforced
at the schema layer for audit rows the same way it is for accounts,
transactions, periods, etc. Today the writer always passes
``household_id`` correctly, but the schema doesn't prevent a future
caller (or a manual ``sqlite3`` shell INSERT) from writing a row whose
``household_id`` points at a tenant that didn't produce it.

The single-column FK to ``households(id)`` stays unchanged — audit_log
has no children that FK back into it, so there's no composite FK to
add elsewhere.

SQLite implementation notes:

- SQLite cannot ALTER a table's PRIMARY KEY in place; alembic's
  ``batch_alter_table`` recreates the table (create new → copy → drop
  old → rename).
- Recreating the table drops the BEFORE UPDATE / BEFORE DELETE
  immutability triggers from #333 (a6f1c9b3d8e4). The migration drops
  them explicitly before the batch and reinstalls them after — leaving
  them in place would silently disappear on tables-recreate, which is
  exactly the kind of "the trigger was here yesterday, where did it go"
  that M-22 is meant to prevent.

Revision ID: c7a3f9e2b1d8
Revises: a6f1c9b3d8e4
Create Date: 2026-05-17 14:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7a3f9e2b1d8"
down_revision: str | None = "a6f1c9b3d8e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRIGGER_NO_UPDATE_SQL = """
CREATE TRIGGER trg_audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only (M-22)');
END;
"""

_TRIGGER_NO_DELETE_SQL = """
CREATE TRIGGER trg_audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only (M-22)');
END;
"""


def upgrade() -> None:
    """Drop immutability triggers, swap PK to composite, reinstall triggers."""
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete")

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_constraint("pk_audit_log", type_="primary")
        batch_op.create_primary_key("pk_audit_log", ["household_id", "id"])

    op.execute(_TRIGGER_NO_UPDATE_SQL)
    op.execute(_TRIGGER_NO_DELETE_SQL)


def downgrade() -> None:
    """Restore the single-column ``id`` PK; reinstall the triggers."""
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete")

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_constraint("pk_audit_log", type_="primary")
        batch_op.create_primary_key("pk_audit_log", ["id"])

    op.execute(_TRIGGER_NO_UPDATE_SQL)
    op.execute(_TRIGGER_NO_DELETE_SQL)
