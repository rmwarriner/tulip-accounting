"""Add allocation_pools, envelopes, sinking_funds, shadow_transactions, shadow_postings.

Implements ADR-0001's shadow-ledger model for envelope and sinking-fund
tracking. Pool balances are derived from `sum(shadow_postings)`; the
balance trigger on `shadow_postings` mirrors the main-ledger triggers in
the initial migration. Also adds the long-deferred FK constraint on
`postings.pool_id` (the column existed since the initial schema as a
nullable BLOB without a referential integrity constraint).

Revision ID: a3f4d8e91b22
Revises: 735bfd334b06
Create Date: 2026-05-02 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.migrations._triggers import (
    INITIAL_TRIGGER_NAMES,
    INITIAL_TRIGGERS,
    P4_0_SHADOW_TRIGGER_NAMES,
    P4_0_SHADOW_TRIGGERS,
)
from tulip_storage.models.base import GUID

revision: str = "a3f4d8e91b22"
down_revision: str | None = "735bfd334b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create allocation/shadow-ledger tables, indexes, triggers, and the postings.pool_id FK."""
    op.create_table(
        "allocation_pools",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column(
            "pool_type",
            sa.Enum(
                "envelope",
                "sinking_fund",
                "inflow",
                "unallocated",
                "spent",
                name="pooltype",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=10),
            nullable=False,
            server_default="shared",
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
            name=op.f("fk_allocation_pools_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_allocation_pools")),
    )
    with op.batch_alter_table("allocation_pools", schema=None) as batch_op:
        batch_op.create_index(
            "ix_allocation_pools_household_active",
            ["household_id", "is_active"],
            unique=False,
        )
        # One Inflow / Unallocated / Spent row per (household, currency).
        batch_op.create_index(
            "ix_allocation_pools_system_per_currency",
            ["household_id", "pool_type", "currency"],
            unique=True,
            sqlite_where=sa.text("is_system = 1"),
        )

    op.create_table(
        "envelopes",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("pool_id", GUID(length=36), nullable=False),
        sa.Column(
            "budget_period",
            sa.Enum(
                "weekly",
                "biweekly",
                "monthly",
                "quarterly",
                "annual",
                "custom",
                name="budgetperiod",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("budget_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column(
            "rollover_policy",
            sa.Enum(
                "reset",
                "accumulate",
                "cap_at_budget",
                name="rolloverpolicy",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("refill_rule_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "pool_id"],
            ["allocation_pools.household_id", "allocation_pools.id"],
            name="fk_envelopes_pool",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "pool_id", name=op.f("pk_envelopes")),
    )

    op.create_table(
        "sinking_funds",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("pool_id", GUID(length=36), nullable=False),
        sa.Column("target_amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column(
            "contribution_strategy",
            sa.Enum(
                "manual",
                "even_split",
                "percentage_of_income",
                name="contributionstrategy",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("contribution_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "pool_id"],
            ["allocation_pools.household_id", "allocation_pools.id"],
            name="fk_sinking_funds_pool",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "pool_id", name=op.f("pk_sinking_funds")),
    )

    op.create_table(
        "shadow_transactions",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "budget_inflow",
                "refill",
                "spend",
                "transfer",
                "rollover",
                "manual",
                name="shadowtxreason",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "posted",
                "voided",
                name="shadowtxstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("paired_main_tx_id", GUID(length=36), nullable=True),
        sa.Column("voided_by_shadow_tx_id", GUID(length=36), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_shadow_transactions_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "paired_main_tx_id"],
            ["transactions.household_id", "transactions.id"],
            name="fk_shadow_transactions_paired_main_tx",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "voided_by_shadow_tx_id"],
            ["shadow_transactions.household_id", "shadow_transactions.id"],
            name="fk_shadow_transactions_voided_by",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_shadow_transactions")),
    )
    with op.batch_alter_table("shadow_transactions", schema=None) as batch_op:
        # Plain ascending index on (household_id, date). DESC scans use the
        # same index in SQLite without needing the explicit DESC modifier.
        batch_op.create_index(
            "ix_shadow_tx_household_date",
            ["household_id", "date"],
            unique=False,
        )
        batch_op.create_index(
            "ix_shadow_tx_paired_main",
            ["household_id", "paired_main_tx_id"],
            unique=False,
            sqlite_where=sa.text("paired_main_tx_id IS NOT NULL"),
        )

    op.create_table(
        "shadow_postings",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("shadow_transaction_id", GUID(length=36), nullable=False),
        sa.Column("pool_id", GUID(length=36), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("memo", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "shadow_transaction_id"],
            ["shadow_transactions.household_id", "shadow_transactions.id"],
            name="fk_shadow_postings_shadow_transaction",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "pool_id"],
            ["allocation_pools.household_id", "allocation_pools.id"],
            name="fk_shadow_postings_pool",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shadow_postings")),
    )
    with op.batch_alter_table("shadow_postings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_shadow_postings_household_id"), ["household_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_shadow_postings_shadow_transaction_id"),
            ["shadow_transaction_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_shadow_postings_pool_id"), ["pool_id"], unique=False)

    # Add the long-deferred FK on postings.pool_id. The column has existed
    # since the initial migration as a nullable GUID without referential
    # integrity. SQLite requires a table rebuild to add an FK; batch_alter_table
    # handles it.
    #
    # The main-ledger balance triggers reference `postings` by name. SQLite's
    # rebuild-rename dance briefly leaves no `postings` table in the database,
    # which makes the trigger bodies unresolvable and aborts the ALTER. Drop
    # the triggers around the rebuild and recreate them afterwards.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger_name in reversed(INITIAL_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    with op.batch_alter_table("postings", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_postings_pool",
            "allocation_pools",
            ["household_id", "pool_id"],
            ["household_id", "id"],
            ondelete="RESTRICT",
        )

    if bind.dialect.name == "sqlite":
        for ddl in INITIAL_TRIGGERS:
            op.execute(ddl)
        for ddl in P4_0_SHADOW_TRIGGERS:
            op.execute(ddl)


def downgrade() -> None:
    """Drop allocation/shadow-ledger tables, the postings.pool_id FK, and triggers."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger_name in reversed(P4_0_SHADOW_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        # Same trigger / batch_alter dance as upgrade(): drop the
        # main-ledger triggers, rebuild the table, recreate the triggers.
        for trigger_name in reversed(INITIAL_TRIGGER_NAMES):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    with op.batch_alter_table("postings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_postings_pool", type_="foreignkey")

    if bind.dialect.name == "sqlite":
        for ddl in INITIAL_TRIGGERS:
            op.execute(ddl)

    with op.batch_alter_table("shadow_postings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_shadow_postings_pool_id"))
        batch_op.drop_index(batch_op.f("ix_shadow_postings_shadow_transaction_id"))
        batch_op.drop_index(batch_op.f("ix_shadow_postings_household_id"))
    op.drop_table("shadow_postings")

    with op.batch_alter_table("shadow_transactions", schema=None) as batch_op:
        batch_op.drop_index("ix_shadow_tx_paired_main")
        batch_op.drop_index("ix_shadow_tx_household_date")
    op.drop_table("shadow_transactions")
    op.drop_table("sinking_funds")
    op.drop_table("envelopes")

    with op.batch_alter_table("allocation_pools", schema=None) as batch_op:
        batch_op.drop_index("ix_allocation_pools_system_per_currency")
        batch_op.drop_index("ix_allocation_pools_household_active")
    op.drop_table("allocation_pools")
