"""Add users.last_totp_step column for TOTP step-replay defence (#330).

Per the security audit's M-5: ``verify_totp_code`` was a stateless
wrapper over ``pyotp.TOTP.verify(valid_window=1)`` — nothing recorded
"this 6-digit code was already accepted." An attacker who observed
a successful TOTP (unencrypted local channel / screen recording /
forgotten log line) had up to ~90 s (±1 window) to replay it from
a different session.

The fix tracks the highest TOTP step Unix-epoch-divided-by-30 that
the user successfully verified. Every subsequent verify refuses
matches at or below that step. The login flow persists the accepted
step in the same commit as session mint.

Column is nullable — users who have never verified a TOTP have NULL,
and the verify path treats NULL as "no replay history yet."

Revision ID: c2a9f4b7e1d8
Revises: c7a3f9e2b1d8
Create Date: 2026-05-17 19:00:00.000000+00:00

Down-revision rebased from ``a6f1c9b3d8e4`` to ``c7a3f9e2b1d8`` to keep
the alembic chain linear after #380 (composite PK) landed in parallel
(see #347 / #338 PR bodies for the merge-order story).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2a9f4b7e1d8"
down_revision: str | None = "c7a3f9e2b1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``users.last_totp_step`` column."""
    with op.batch_alter_table("users", schema=None) as batch:
        batch.add_column(sa.Column("last_totp_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Drop the ``users.last_totp_step`` column."""
    with op.batch_alter_table("users", schema=None) as batch:
        batch.drop_column("last_totp_step")
