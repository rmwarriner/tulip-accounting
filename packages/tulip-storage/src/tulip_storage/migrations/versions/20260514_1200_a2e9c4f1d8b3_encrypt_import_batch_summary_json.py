"""Encrypt import_batches.summary_json at rest.

Per #238: the plaintext ``summary_json`` JSON column is shaped to carry
source-bank identifiers (OFX BANKID/ACCTID) — sensitive data that would
sit in plaintext parallel to the encrypted ``accounts.external_account_
number_encrypted`` column. Replace it with ``summary_json_encrypted``
(LargeBinary), written via ``encryption.encrypt_field`` in
``ImportBatchRepository``.

Existing ``summary_json`` values today only ever contain
``{"line_count": int}`` — non-sensitive, and never read by any API/CLI
consumer. Per #238's out-of-scope note ("accept that pre-fix rows remain
plaintext; don't re-encrypt old rows"), the old values are dropped with
the old column rather than migrated.

Revision ID: a2e9c4f1d8b3
Revises: c1d4f7a2b9e6
Create Date: 2026-05-14 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2e9c4f1d8b3"
down_revision: str | None = "c1d4f7a2b9e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the plaintext summary_json column with an encrypted blob."""
    with op.batch_alter_table("import_batches", schema=None) as batch:
        batch.drop_column("summary_json")
        batch.add_column(sa.Column("summary_json_encrypted", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    """Restore the plaintext JSON column (encrypted values are not recoverable)."""
    with op.batch_alter_table("import_batches", schema=None) as batch:
        batch.drop_column("summary_json_encrypted")
        batch.add_column(sa.Column("summary_json", sa.JSON(), nullable=True))
