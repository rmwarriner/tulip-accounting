"""Add SQLite immutability triggers for ``audit_log`` (#333).

Per security audit M-22: the ``audit_log`` model docstring notes that
DB-level immutability is deferred to the Postgres phase. App-level
enforcement (no UPDATE / DELETE statement against the table exists
anywhere in the writer or callers) is correct today — but an attacker
with shell access to ``tulip.db`` can run
``sqlite3 tulip.db "DELETE FROM audit_log WHERE …"`` undetectably.

Two triggers ship here:

- ``trg_audit_log_no_update`` — BEFORE UPDATE, unconditional ABORT.
  audit_log is true append-only; the writer only INSERTs.

- ``trg_audit_log_no_delete`` — BEFORE DELETE, unconditional ABORT.
  Legitimate cascade-delete sites (household-erasure, audit-retention
  prune) drop the trigger via the
  :func:`tulip_storage.audit_log_helpers.audit_log_deletion_allowed`
  context manager and recreate it after. SQLite has a documented
  limitation that triggers cannot reference TEMP objects, so the
  temp-marker pattern used elsewhere isn't available; drop-and-
  recreate is the documented fallback.

The trigger is defense-in-depth against application-layer regressions
and accidental ``sqlite3`` shell DELETEs, not a hardened ACL. An
attacker with raw shell access can drop the trigger themselves; the
point is to make the bypass visible.

Revision ID: a6f1c9b3d8e4
Revises: d9e2c8b5a3f7
Create Date: 2026-05-17 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "a6f1c9b3d8e4"
down_revision: str | None = "d9e2c8b5a3f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Trigger DDL — exported so the runtime helper can recreate after the
#: legitimate cascade-delete sites finish.
TRIGGER_NO_UPDATE_SQL: Final[str] = """
CREATE TRIGGER trg_audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only (M-22)');
END;
"""

TRIGGER_NO_DELETE_SQL: Final[str] = """
CREATE TRIGGER trg_audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only (M-22)');
END;
"""


def upgrade() -> None:
    """Install the BEFORE UPDATE + BEFORE DELETE immutability triggers."""
    op.execute(TRIGGER_NO_UPDATE_SQL)
    op.execute(TRIGGER_NO_DELETE_SQL)


def downgrade() -> None:
    """Drop both triggers."""
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete")
