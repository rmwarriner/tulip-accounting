"""Initial schema for tulip-storage.

Creates households, users, accounts, periods, transactions, postings,
and audit_log. Adds a SQLite trigger that enforces the double-entry
balance invariant when a transaction is posted (status transitions to
POSTED or RECONCILED, or postings are modified on an already-posted
transaction). The application layer validates the same invariant before
ever issuing the UPDATE; the trigger is defense in depth against direct
DB writes.

Revision ID: c2f963036df3
Revises:
Create Date: 2026-04-29 21:16:58.258736+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from tulip_storage.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = "c2f963036df3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# SQL for the balanced-postings trigger. Fires when a transaction's status
# transitions to POSTED/RECONCILED, or when postings are mutated on an
# already-posted transaction. Pending transactions may carry unbalanced
# postings (e.g., import-time staging).
_TRIGGER_TX_STATUS_BALANCE = """
CREATE TRIGGER trg_transactions_balanced_on_post
AFTER UPDATE OF status ON transactions
WHEN NEW.status IN ('POSTED', 'RECONCILED')
  AND (OLD.status IS NULL OR OLD.status != NEW.status)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = NEW.household_id
        AND transaction_id = NEW.id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'transaction postings do not balance per currency')
  END;
END;
"""

_TRIGGER_POSTING_INSERT_BALANCE = """
CREATE TRIGGER trg_postings_balanced_on_insert
AFTER INSERT ON postings
WHEN EXISTS (
  SELECT 1 FROM transactions
  WHERE household_id = NEW.household_id
    AND id = NEW.transaction_id
    AND status IN ('POSTED', 'RECONCILED')
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = NEW.household_id
        AND transaction_id = NEW.transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'cannot insert posting that breaks balance on a posted transaction')
  END;
END;
"""

_TRIGGER_POSTING_UPDATE_BALANCE = """
CREATE TRIGGER trg_postings_balanced_on_update
AFTER UPDATE ON postings
WHEN EXISTS (
  SELECT 1 FROM transactions
  WHERE household_id = NEW.household_id
    AND id = NEW.transaction_id
    AND status IN ('POSTED', 'RECONCILED')
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = NEW.household_id
        AND transaction_id = NEW.transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'cannot update posting that breaks balance on a posted transaction')
  END;
END;
"""

_TRIGGER_POSTING_DELETE_BALANCE = """
CREATE TRIGGER trg_postings_balanced_on_delete
AFTER DELETE ON postings
WHEN EXISTS (
  SELECT 1 FROM transactions
  WHERE household_id = OLD.household_id
    AND id = OLD.transaction_id
    AND status IN ('POSTED', 'RECONCILED')
)
BEGIN
  SELECT CASE
    WHEN EXISTS (
      SELECT 1 FROM postings
      WHERE household_id = OLD.household_id
        AND transaction_id = OLD.transaction_id
      GROUP BY currency
      HAVING SUM(amount) != 0
    )
    THEN RAISE(ABORT, 'cannot delete posting that breaks balance on a posted transaction')
  END;
END;
"""


def upgrade() -> None:
    """Create all Phase-1 tables, indexes, and the balance triggers."""
    op.create_table(
        "households",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_households")),
    )
    op.create_table(
        "accounts",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("parent_account_id", GUID(length=36), nullable=True),
        sa.Column("code", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "ASSET",
                "LIABILITY",
                "EQUITY",
                "INCOME",
                "EXPENSE",
                name="accounttype",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("subtype", sa.String(length=50), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("visibility", sa.String(length=10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("external_account_number_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("notes_encrypted", sa.LargeBinary(), nullable=True),
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
            ["household_id", "parent_account_id"],
            ["accounts.household_id", "accounts.id"],
            name="fk_accounts_parent",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_accounts_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_accounts")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", GUID(length=36), nullable=True),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", GUID(length=36), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("request_id", GUID(length=36), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_audit_log_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audit_log_household_id"), ["household_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_audit_log_occurred_at"), ["occurred_at"], unique=False)

    op.create_table(
        "periods",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "SOFT_CLOSED",
                name="periodstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("closed_by_user_id", GUID(length=36), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
            name=op.f("fk_periods_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_periods")),
    )
    op.create_table(
        "transactions",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "POSTED",
                "RECONCILED",
                name="transactionstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("notes_encrypted", sa.LargeBinary(), nullable=True),
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
            name=op.f("fk_transactions_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_transactions")),
    )
    op.create_table(
        "users",
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "ADMIN",
                "MEMBER",
                "VIEWER",
                name="userrole",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_users_household_id_households"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "id", name=op.f("pk_users")),
        sa.UniqueConstraint("household_id", "email", name="uq_users_household_email"),
    )
    op.create_table(
        "postings",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("household_id", GUID(length=36), nullable=False),
        sa.Column("transaction_id", GUID(length=36), nullable=False),
        sa.Column("account_id", GUID(length=36), nullable=False),
        sa.Column("pool_id", GUID(length=36), nullable=True),
        sa.Column("amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("fx_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("fx_currency", sa.String(length=3), nullable=True),
        sa.Column("memo", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            name="fk_postings_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            name="fk_postings_transaction",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postings")),
    )
    with op.batch_alter_table("postings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_postings_account_id"), ["account_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_postings_household_id"), ["household_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_postings_transaction_id"),
            ["transaction_id"],
            unique=False,
        )

    # Balance-enforcement triggers — SQLite-specific.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(_TRIGGER_TX_STATUS_BALANCE)
        op.execute(_TRIGGER_POSTING_INSERT_BALANCE)
        op.execute(_TRIGGER_POSTING_UPDATE_BALANCE)
        op.execute(_TRIGGER_POSTING_DELETE_BALANCE)


def downgrade() -> None:
    """Drop all triggers, indexes, and tables in reverse order."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_postings_balanced_on_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_postings_balanced_on_update")
        op.execute("DROP TRIGGER IF EXISTS trg_postings_balanced_on_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_transactions_balanced_on_post")

    with op.batch_alter_table("postings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_postings_transaction_id"))
        batch_op.drop_index(batch_op.f("ix_postings_household_id"))
        batch_op.drop_index(batch_op.f("ix_postings_account_id"))
    op.drop_table("postings")
    op.drop_table("users")
    op.drop_table("transactions")
    op.drop_table("periods")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_log_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_audit_log_household_id"))
    op.drop_table("audit_log")
    op.drop_table("accounts")
    op.drop_table("households")
