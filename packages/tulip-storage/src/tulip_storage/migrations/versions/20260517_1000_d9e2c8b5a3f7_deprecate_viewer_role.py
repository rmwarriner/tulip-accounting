"""Deprecate ``UserRole.VIEWER`` — never wired through ``require_role`` (#341).

Per the deep privacy audit's M-26: ``UserRole.VIEWER`` exists in the
enum but no router accepts it and ``_filter_for_role`` only
special-cases ``"admin"``. A future read-only role cannot be safely
built on top of the nominal-only scaffolding — re-introducing it
cleanly once a real read-only-audit-seat use case lands is straight-
forward, and the misleading current shape is worse than the absence.

Defensive UPDATE handles any extant VIEWER rows (idempotent — zero on
a fresh install) by demoting them to ``MEMBER``. ``batch_alter_table``
then rewrites the ``users.role`` column without ``VIEWER`` in the
CHECK constraint.

Revision ID: d9e2c8b5a3f7
Revises: b4c8e2d9a1f5
Create Date: 2026-05-17 10:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e2c8b5a3f7"
down_revision: str | None = "b4c8e2d9a1f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_ENUM = sa.Enum("ADMIN", "MEMBER", "VIEWER", name="userrole", native_enum=False, length=20)
_NEW_ENUM = sa.Enum("ADMIN", "MEMBER", name="userrole", native_enum=False, length=20)


def upgrade() -> None:
    """Demote VIEWER rows to MEMBER, then narrow the CHECK constraint."""
    op.execute("UPDATE users SET role = 'MEMBER' WHERE role = 'VIEWER'")
    with op.batch_alter_table("users", schema=None) as batch:
        batch.alter_column(
            "role",
            existing_type=_OLD_ENUM,
            type_=_NEW_ENUM,
            existing_nullable=False,
        )


def downgrade() -> None:
    """Re-widen the CHECK constraint to allow VIEWER again."""
    with op.batch_alter_table("users", schema=None) as batch:
        batch.alter_column(
            "role",
            existing_type=_NEW_ENUM,
            type_=_OLD_ENUM,
            existing_nullable=False,
        )
