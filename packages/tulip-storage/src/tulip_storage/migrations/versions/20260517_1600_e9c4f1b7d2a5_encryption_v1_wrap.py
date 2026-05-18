"""Prefix every legacy field-encrypted blob with the v1 version byte (#338).

Pre-#338, ``encrypt_field`` wrote raw ``nonce(12) || ct + tag(16)`` with
``associated_data=None`` — no version prefix. Post-#338, ``encrypt_field``
writes the v2 wire format ``0x02 || nonce || ct + tag`` with caller-
supplied AAD. To dispatch reliably on read, every blob at rest needs an
explicit version byte; without it, the leading byte (a random nonce in
the legacy shape) collides with v2's ``0x02`` 1/256 of the time.

This migration walks every column known to hold a v1 blob and prefixes
each non-NULL value with ``0x01`` (the legacy-no-AAD marker that
``decrypt_field`` understands). The plaintext is untouched — no master
key is required to run the migration.

Attachment ciphertext files on disk are *not* rewritten here: the
attachment GC runs at the filesystem level, and the count of blob files
on a real install can be much larger than the DB row count. The
``AttachmentRepository.read_bytes`` path uses :func:`wrap_legacy_v1_blob`
on the read side to add the v1 prefix in memory if the file lacks one.
(That logic lands in the same PR; this migration only covers DB columns.)

Idempotent: re-running this migration is safe because
:func:`wrap_legacy_v1_blob` returns its input unchanged when it already
starts with a recognised version byte.

Revision ID: e9c4f1b7d2a5
Revises: c2a9f4b7e1d8
Create Date: 2026-05-17 16:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9c4f1b7d2a5"
down_revision: str | None = "c2a9f4b7e1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: ``(table, column)`` pairs that hold v1 field-encrypted blobs that need
#: the ``0x01`` prefix added on upgrade. Keep in sync with new encrypted
#: columns added in future migrations.
_TARGETS: tuple[tuple[str, str], ...] = (
    ("users", "totp_secret_encrypted"),
    ("users", "ai_keys_encrypted"),
    ("accounts", "external_account_number_encrypted"),
    ("accounts", "notes_encrypted"),
    ("transactions", "notes_encrypted"),
    ("households", "ai_keys_encrypted"),
    ("import_batches", "summary_json_encrypted"),
)


def upgrade() -> None:
    """Prefix every non-NULL legacy blob in ``_TARGETS`` with ``0x01``.

    Reads the column, prepends the version byte in Python, writes back.
    Skips rows whose blob already starts with a known version byte
    (idempotent re-run).
    """
    from tulip_storage.encryption import wrap_legacy_v1_blob

    conn = op.get_bind()
    for table, column in _TARGETS:
        # Skip tables that don't exist yet (defensive — early migration
        # in the chain may have created columns we don't yet need).
        inspector = sa.inspect(conn)
        if table not in inspector.get_table_names():
            continue
        col_names = {c["name"] for c in inspector.get_columns(table)}
        if column not in col_names:
            continue

        # Pick a PK we can address rows by. SQLite + most other backends
        # let us update by ROWID; we use (household_id, id) where present,
        # falling back to id.
        rows = conn.execute(
            sa.text(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
        ).fetchall()
        for rowid, blob in rows:
            if blob is None:
                continue
            wrapped = wrap_legacy_v1_blob(bytes(blob))
            if wrapped == bytes(blob):
                continue  # already version-prefixed
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :b WHERE rowid = :r"),  # noqa: S608
                {"b": wrapped, "r": rowid},
            )


def downgrade() -> None:
    """Strip the ``0x01`` prefix from every blob, restoring the pre-#338 shape.

    The strip is conservative: only blobs whose leading byte is exactly
    ``0x01`` are altered. ``0x02`` (v2) blobs are NOT touched — they
    cannot be safely "downgraded" without knowing the master key, since
    v2 is AEAD-bound to AAD components the migration can't reconstruct.
    Downgrading after any v2 write therefore leaves those rows stranded
    in v2 shape; document this and require operators to confirm the
    downgrade scope.
    """
    conn = op.get_bind()
    for table, column in _TARGETS:
        inspector = sa.inspect(conn)
        if table not in inspector.get_table_names():
            continue
        col_names = {c["name"] for c in inspector.get_columns(table)}
        if column not in col_names:
            continue

        rows = conn.execute(
            sa.text(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
        ).fetchall()
        for rowid, blob in rows:
            if blob is None or len(blob) == 0:
                continue
            if blob[0] != 0x01:
                continue
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :b WHERE rowid = :r"),  # noqa: S608
                {"b": bytes(blob[1:]), "r": rowid},
            )
