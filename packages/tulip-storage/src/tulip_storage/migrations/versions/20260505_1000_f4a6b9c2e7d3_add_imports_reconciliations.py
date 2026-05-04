"""Add imports + reconciliations storage layer (P5.1).

Per ADR-0004 §"Schema (P5.1 migration sketch)". Adds 7 new tables:
``attachments``, ``attachment_links``, ``import_batches``,
``statement_lines``, ``reconciliations``, ``reconciliation_matches``,
``csv_profiles``. Adds 5 nullable columns on ``transactions``:
``cleared_at``, ``reconciled_at``, ``reconciliation_id``,
``imported_from_id``, ``carried_forward_from_reconciliation_id``, plus
3 composite FKs.

The 5 new ``transactions`` columns require a SQLite ``batch_alter_table``
rebuild. The main-ledger balance triggers reference ``transactions`` by
name and would fail mid-rebuild; same trigger drop-and-recreate dance as
P5.0's ``20260504_2000_e7d2a4f8c1b9_add_transaction_void_links.py``.

Revision ID: f4a6b9c2e7d3
Revises: e7d2a4f8c1b9
Create Date: 2026-05-05 10:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.migrations._triggers import (
    INITIAL_TRIGGER_NAMES,
    INITIAL_TRIGGERS,
)
from tulip_storage.models.base import GUID

revision: str = "f4a6b9c2e7d3"
down_revision: str | None = "e7d2a4f8c1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create 7 new tables + 5 new transactions columns + 3 composite FKs."""
    # -------- attachments --------
    op.create_table(
        "attachments",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_uri", sa.String(length=500), nullable=False),
        sa.Column("data_key_wrapped", sa.LargeBinary(), nullable=True),
        sa.Column("uploaded_by_user_id", GUID(length=36), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_attachments_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_attachments")),
    )
    with op.batch_alter_table("attachments", schema=None) as batch_op:
        batch_op.create_index(
            "ix_attachments_hash",
            ["household_id", "content_hash"],
            unique=True,
        )

    # -------- attachment_links --------
    op.create_table(
        "attachment_links",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("attachment_id", GUID(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", GUID(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "attachment_id"],
            ["attachments.household_id", "attachments.id"],
            name="fk_attachment_links_attachment",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "entity_type IN ('transaction', 'account', 'reconciliation', "
            "'sinking_fund', 'import_batch')",
            name="ck_attachment_links_entity_type",
        ),
        sa.PrimaryKeyConstraint(
            "household_id",
            "attachment_id",
            "entity_type",
            "entity_id",
            name=op.f("pk_attachment_links"),
        ),
    )

    # -------- import_batches --------
    op.create_table(
        "import_batches",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("account_id", GUID(length=36), nullable=False),
        sa.Column(
            "source_format",
            sa.Enum(
                "ofx",
                "qif",
                "csv",
                "journal",
                name="sourceformat",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("source_file_attachment_id", GUID(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "parsed",
                "applied",
                "reverted",
                name="importbatchstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", GUID(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            name="fk_import_batches_account",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "source_file_attachment_id"],
            ["attachments.household_id", "attachments.id"],
            name="fk_import_batches_attachment",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_import_batches")),
    )
    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.create_index(
            "ix_import_batches_idempotency",
            ["household_id", "account_id", "source_file_attachment_id"],
            unique=True,
        )

    # -------- statement_lines --------
    op.create_table(
        "statement_lines",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("import_batch_id", GUID(length=36), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("counterparty", sa.String(length=500), nullable=True),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("fitid", sa.String(length=200), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column(
            "is_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("reconciliation_match_id", GUID(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "import_batch_id"],
            ["import_batches.household_id", "import_batches.id"],
            name="fk_statement_lines_import_batch",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_statement_lines")),
    )
    with op.batch_alter_table("statement_lines", schema=None) as batch_op:
        batch_op.create_index(
            "ix_statement_lines_batch",
            ["household_id", "import_batch_id"],
            unique=False,
        )
        # Hot path: unmatched-inbox queries scan this index.
        batch_op.create_index(
            "ix_statement_lines_unmatched",
            ["household_id", "import_batch_id"],
            unique=False,
            sqlite_where=sa.text("reconciliation_match_id IS NULL AND is_excluded = 0"),
        )

    # -------- reconciliations --------
    op.create_table(
        "reconciliations",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("account_id", GUID(length=36), nullable=False),
        sa.Column("statement_period_start", sa.Date(), nullable=False),
        sa.Column("statement_period_end", sa.Date(), nullable=False),
        sa.Column(
            "statement_starting_balance",
            sa.Numeric(precision=20, scale=8),
            nullable=False,
        ),
        sa.Column(
            "statement_ending_balance",
            sa.Numeric(precision=20, scale=8),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "in_progress",
                "complete",
                "abandoned",
                name="reconciliationstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("source_import_batch_id", GUID(length=36), nullable=True),
        sa.Column("created_by_user_id", GUID(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            name="fk_reconciliations_account",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "source_import_batch_id"],
            ["import_batches.household_id", "import_batches.id"],
            name="fk_reconciliations_import_batch",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_reconciliations")),
    )
    with op.batch_alter_table("reconciliations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_reconciliations_account",
            ["household_id", "account_id", "statement_period_end"],
            unique=False,
        )

    # -------- reconciliation_matches --------
    op.create_table(
        "reconciliation_matches",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("reconciliation_id", GUID(length=36), nullable=False),
        sa.Column("statement_line_id", GUID(length=36), nullable=False),
        sa.Column("ledger_transaction_id", GUID(length=36), nullable=False),
        sa.Column("match_amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "confidence",
            sa.Enum(
                "high",
                "medium",
                "low",
                name="matchconfidence",
                native_enum=False,
                length=10,
            ),
            nullable=True,
        ),
        sa.Column("matcher_version", sa.String(length=50), nullable=True),
        sa.Column("created_by_user_id", GUID(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "reconciliation_id"],
            ["reconciliations.household_id", "reconciliations.id"],
            name="fk_reconciliation_matches_reconciliation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "statement_line_id"],
            ["statement_lines.household_id", "statement_lines.id"],
            name="fk_reconciliation_matches_statement_line",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "ledger_transaction_id"],
            ["transactions.household_id", "transactions.id"],
            name="fk_reconciliation_matches_transaction",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_reconciliation_matches")),
    )
    with op.batch_alter_table("reconciliation_matches", schema=None) as batch_op:
        batch_op.create_index(
            "ix_reconciliation_matches_recon",
            ["household_id", "reconciliation_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_reconciliation_matches_tx",
            ["household_id", "ledger_transaction_id"],
            unique=False,
        )

    # -------- csv_profiles --------
    op.create_table(
        "csv_profiles",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("yaml_body", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", GUID(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_csv_profiles_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_csv_profiles")),
    )
    with op.batch_alter_table("csv_profiles", schema=None) as batch_op:
        batch_op.create_index(
            "ix_csv_profiles_name",
            ["household_id", "name"],
            unique=True,
        )

    # -------- transactions: 5 new columns + 3 composite FKs --------
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger_name in reversed(INITIAL_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("reconciliation_id", GUID(length=36), nullable=True))
        batch_op.add_column(sa.Column("imported_from_id", GUID(length=36), nullable=True))
        batch_op.add_column(
            sa.Column(
                "carried_forward_from_reconciliation_id",
                GUID(length=36),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_transactions_reconciliation",
            "reconciliations",
            ["household_id", "reconciliation_id"],
            ["household_id", "id"],
            use_alter=True,
        )
        batch_op.create_foreign_key(
            "fk_transactions_imported_from",
            "import_batches",
            ["household_id", "imported_from_id"],
            ["household_id", "id"],
            use_alter=True,
        )
        batch_op.create_foreign_key(
            "fk_transactions_carried_forward_from",
            "reconciliations",
            ["household_id", "carried_forward_from_reconciliation_id"],
            ["household_id", "id"],
            use_alter=True,
        )

    if bind.dialect.name == "sqlite":
        for ddl in INITIAL_TRIGGERS:
            op.execute(ddl)


def downgrade() -> None:
    """Drop everything P5.1 added, in reverse-dependency order."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger_name in reversed(INITIAL_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_transactions_carried_forward_from", type_="foreignkey")
        batch_op.drop_constraint("fk_transactions_imported_from", type_="foreignkey")
        batch_op.drop_constraint("fk_transactions_reconciliation", type_="foreignkey")
        batch_op.drop_column("carried_forward_from_reconciliation_id")
        batch_op.drop_column("imported_from_id")
        batch_op.drop_column("reconciliation_id")
        batch_op.drop_column("reconciled_at")
        batch_op.drop_column("cleared_at")

    if bind.dialect.name == "sqlite":
        for ddl in INITIAL_TRIGGERS:
            op.execute(ddl)

    with op.batch_alter_table("csv_profiles", schema=None) as batch_op:
        batch_op.drop_index("ix_csv_profiles_name")
    op.drop_table("csv_profiles")

    with op.batch_alter_table("reconciliation_matches", schema=None) as batch_op:
        batch_op.drop_index("ix_reconciliation_matches_tx")
        batch_op.drop_index("ix_reconciliation_matches_recon")
    op.drop_table("reconciliation_matches")

    with op.batch_alter_table("reconciliations", schema=None) as batch_op:
        batch_op.drop_index("ix_reconciliations_account")
    op.drop_table("reconciliations")

    with op.batch_alter_table("statement_lines", schema=None) as batch_op:
        batch_op.drop_index("ix_statement_lines_unmatched")
        batch_op.drop_index("ix_statement_lines_batch")
    op.drop_table("statement_lines")

    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.drop_index("ix_import_batches_idempotency")
    op.drop_table("import_batches")

    op.drop_table("attachment_links")

    with op.batch_alter_table("attachments", schema=None) as batch_op:
        batch_op.drop_index("ix_attachments_hash")
    op.drop_table("attachments")
