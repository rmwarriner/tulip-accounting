"""Normalise tags: central ``tags`` table + refactor ``transaction_tags`` FK (ADR-0009).

PR A of the tag redesign. Migrates from the freeform string surface
(#39) to a normalised tag registry that PRs B and C extend with
posting + account scopes and inheritance.

Migration is lossless:

1. Create the ``tags`` table.
2. Backfill: for each distinct ``(household_id, tag)`` row in the
   existing ``transaction_tags``, insert a row in ``tags`` with a
   fresh UUID.
3. Rebuild ``transaction_tags`` with a ``tag_id`` column (SQLite
   batch_alter_table — no ALTER COLUMN), populated via JOIN against
   the lookup we just built.
4. Drop the old ``tag`` string column and the legacy filter index
   (the new composite PK + the FK on ``tag_id`` cover both lookups).

Revision ID: b2c4f9a1e7d6
Revises: a8b3c2d1f4e5
Create Date: 2026-05-21 00:00:00.000000+00:00
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c4f9a1e7d6"
down_revision: str | None = "a8b3c2d1f4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``tags`` + refactor ``transaction_tags`` to FK by tag_id."""
    # 1. Create the central tag registry.
    op.create_table(
        "tags",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("household_id", "id", name="pk_tags"),
        sa.UniqueConstraint("household_id", "name", name="uq_tags_household_name"),
    )

    # 2. Backfill: walk existing transaction_tags, dedupe by
    # (household_id, tag), insert into ``tags``. Build the lookup
    # in Python so we can use uuid4() per row — driver-neutral
    # (SQLite has no gen_random_uuid()).
    bind = op.get_bind()
    existing_pairs = bind.execute(
        sa.text("SELECT DISTINCT household_id, tag FROM transaction_tags")
    ).fetchall()
    name_to_id: dict[tuple[bytes | str, str], str] = {}
    for household_id, tag in existing_pairs:
        new_id = str(uuid.uuid4())
        name_to_id[(household_id, tag)] = new_id
        bind.execute(
            sa.text(
                "INSERT INTO tags (household_id, id, name, created_at) "
                "VALUES (:hid, :tid, :name, CURRENT_TIMESTAMP)"
            ),
            {"hid": household_id, "tid": new_id, "name": tag},
        )

    # 3. Rebuild ``transaction_tags`` with the new schema. SQLite
    # doesn't support ALTER COLUMN, so batch_alter_table recreates
    # the table via copy. The drop-and-recreate also clears the
    # legacy ``ix_transaction_tags_household_tag`` index.
    with op.batch_alter_table("transaction_tags") as batch:
        batch.drop_index("ix_transaction_tags_household_tag")

    # Capture existing rows so we can re-insert with tag_id.
    existing_rows = bind.execute(
        sa.text("SELECT household_id, transaction_id, tag FROM transaction_tags")
    ).fetchall()

    op.drop_table("transaction_tags")
    op.create_table(
        "transaction_tags",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_transaction",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "tag_id"],
            ["tags.household_id", "tags.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_tag",
        ),
        sa.PrimaryKeyConstraint(
            "household_id", "transaction_id", "tag_id", name="pk_transaction_tags"
        ),
    )

    # 4. Re-insert each historical row with its resolved tag_id.
    for household_id, transaction_id, tag in existing_rows:
        tag_id = name_to_id[(household_id, tag)]
        bind.execute(
            sa.text(
                "INSERT INTO transaction_tags "
                "(household_id, transaction_id, tag_id) "
                "VALUES (:hid, :tx, :tag_id)"
            ),
            {"hid": household_id, "tx": transaction_id, "tag_id": tag_id},
        )


def downgrade() -> None:
    """Reverse: dehydrate tag_id back to the inline string."""
    bind = op.get_bind()

    # Snapshot the new shape's data with names resolved.
    rows = bind.execute(
        sa.text(
            "SELECT tt.household_id, tt.transaction_id, t.name "
            "FROM transaction_tags AS tt "
            "JOIN tags AS t "
            "  ON t.household_id = tt.household_id AND t.id = tt.tag_id"
        )
    ).fetchall()

    op.drop_table("transaction_tags")
    op.create_table(
        "transaction_tags",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_transaction_tags_transaction",
        ),
        sa.PrimaryKeyConstraint(
            "household_id", "transaction_id", "tag", name="pk_transaction_tags"
        ),
    )
    op.create_index(
        "ix_transaction_tags_household_tag",
        "transaction_tags",
        ["household_id", "tag"],
    )
    for household_id, transaction_id, tag in rows:
        bind.execute(
            sa.text(
                "INSERT INTO transaction_tags "
                "(household_id, transaction_id, tag) "
                "VALUES (:hid, :tx, :tag)"
            ),
            {"hid": household_id, "tx": transaction_id, "tag": tag},
        )
    op.drop_table("tags")
